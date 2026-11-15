"""Canonical, checkpointed Alien state and transactional deterministic commands.

Only this module writes Alien state. Resolvers receive detached JSON data and
an explicit RNG; failed commands cannot leak partially applied effects.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import secrets

from lorekit.db import LoreKitError

SYSTEM = "alien-evolved"
PROFILE = "evolved-beta2"
VERSION = 1
KEY = "alien_evolved_state"


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value, name: str, low: int = 0, high: int | None = None) -> int:
    if type(value) is not int or value < low or (high is not None and value > high):
        raise LoreKitError(f"{name} must be an integer in {low}..{high if high is not None else 'unbounded'}")
    return value


def require_session(db, session_id: int) -> None:
    integer(session_id, "session_id", 1)
    row = db.execute("SELECT system_type FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None or row[0] != SYSTEM:
        raise LoreKitError("Alien operations require an alien-evolved session")


def initial() -> dict:
    return {
        "profile": PROFILE,
        "version": VERSION,
        "revision": 0,
        "content_hash": None,
        "actors": {},
        "scene": None,
        "mode": "social",
        "combat": None,
        "pending": None,
        "queue": [],
        "clocks": {"round": 0, "stretch": 0, "shift": 0, "day": 0},
        "options": {"mental_trauma": False, "aliens_mode": False, "fixed_initiative": False},
    }


def load(db, session_id: int) -> dict:
    require_session(db, session_id)
    row = db.execute("SELECT value FROM session_meta WHERE session_id = ? AND key = ?", (session_id, KEY)).fetchone()
    record = json.loads(row[0]) if row else {"base": initial(), "state": initial(), "audit": []}
    state = record["state"]
    validate_snapshot(state)
    if state.get("profile") != PROFILE or state.get("version") != VERSION:
        raise LoreKitError("Incompatible Alien rules profile/version; explicit migration required")
    return record


def actor(state, actor_id) -> dict:
    if not isinstance(actor_id, str) or actor_id not in state["actors"]:
        raise LoreKitError("Actor not found in this Alien session")
    return state["actors"][actor_id]


def projection(state: dict, audience: str = "public") -> dict:
    if audience not in ("public", "gm"):
        raise LoreKitError("audience must be public or gm")
    if audience == "gm":
        return {"visibility": "gm_only", **copy.deepcopy(state)}
    # An allowlist is intentional: new private fields cannot leak by default.
    actors = {}
    for aid, a in state["actors"].items():
        if not a.get("revealed", a.get("kind") == "pc"):
            continue
        visible = {"id": aid, "name": a["name"]}
        if a.get("kind") == "pc" and not a.get("secret_identity"):
            visible.update({key: copy.deepcopy(a[key]) for key in ("health", "health_max", "stress", "story_points")})
        if not a.get("hidden", False):
            visible["zone"] = a.get("zone")
        actors[aid] = visible
    return {
        "profile": state["profile"],
        "version": state["version"],
        "revision": state["revision"],
        "mode": state["mode"],
        "actors": actors,
    }


def execute(
    db, session_id: int, intent: dict, request_id: str, expected_revision: int, seed: int | None = None
) -> dict:
    """Execute one command; request identity includes caller-provided seed.

    Caller-owned transactions remain caller-owned. A fresh connection acquires
    a write lock before checking its revision, so concurrent clients cannot both
    accept a command at the same revision.
    """
    from lorekit.alien.engine import transition

    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise LoreKitError("request_id must be a nonempty string of at most 128 characters")
    integer(expected_revision, "expected_revision")
    if seed is not None:
        integer(seed, "seed")
    if not isinstance(intent, dict):
        raise LoreKitError("intent must be an object")
    signature = digest({"intent": intent, "seed": seed, "expected_revision": expected_revision})
    owns = not db.in_transaction
    if owns:
        db.execute("BEGIN IMMEDIATE")
    db.execute("SAVEPOINT alien_command")
    try:
        record = load(db, session_id)
        for entry in record["audit"]:
            if entry["request_id"] == request_id:
                if signature != entry["signature"]:
                    raise LoreKitError("request_id already used for different input")
                result = copy.deepcopy(entry["result"])
                db.execute("RELEASE alien_command")
                if owns:
                    db.commit()
                return result
        state = record["state"]
        if state["revision"] != expected_revision:
            raise LoreKitError("Stale Alien revision; reload status before retrying")
        actual_seed = secrets.randbits(63) if seed is None else seed
        working = copy.deepcopy(state)
        effective = copy.deepcopy(intent)
        if effective.get("op") in ("character_create", "character_import", "creature_import"):
            from lorekit.alien import characters, content

            if effective["op"] == "creature_import":
                from lorekit.alien.creatures import build

                built = build(effective.get("template"), effective.get("name"), content.read(working["content_hash"]))
                name, kind = built["name"], "npc"
            else:
                spec = effective.get("build")
                characters.build(
                    spec, content.read(working["content_hash"]), imported=effective["op"] == "character_import"
                )
                name, kind = spec["name"], spec.get("kind", "pc")
            cur = db.execute(
                "INSERT INTO characters (session_id, name, level, type, prefetch) VALUES (?, ?, 1, ?, ?)",
                (session_id, name, kind, int(kind == "pc")),
            )
            effective["character_id"] = cur.lastrowid
        result = transition(working, effective, random.Random(actual_seed))
        working["revision"] += 1
        validate_snapshot(working)
        result = {
            "visibility": "gm_only",
            "profile": PROFILE,
            "version": VERSION,
            "content_hash": working["content_hash"],
            "revision": working["revision"],
            "request_id": request_id,
            "seed": actual_seed,
            "pending": copy.deepcopy(working["pending"]),
            "queue_count": len(working["queue"]),
            **result,
        }
        entry = {
            "request_id": request_id,
            "signature": signature,
            "intent": effective,
            "seed": actual_seed,
            "result": result,
            "digest": digest(working),
        }
        record["audit"].append(entry)
        record["state"] = working
        db.execute(
            "INSERT INTO session_meta (session_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value",
            (session_id, KEY, canonical(record)),
        )
        db.execute("RELEASE alien_command")
        if owns:
            db.commit()
        return copy.deepcopy(result)
    except Exception:
        db.execute("ROLLBACK TO alien_command")
        db.execute("RELEASE alien_command")
        if owns:
            db.rollback()
        raise


def verify(record: dict) -> None:
    from lorekit.alien.engine import transition

    state = copy.deepcopy(record["base"])
    for index, entry in enumerate(record["audit"]):
        result = transition(state, copy.deepcopy(entry["intent"]), random.Random(entry["seed"]))
        state["revision"] += 1
        expected_result = {
            "visibility": "gm_only",
            "profile": PROFILE,
            "version": VERSION,
            "content_hash": state["content_hash"],
            "revision": state["revision"],
            "request_id": entry["request_id"],
            "seed": entry["seed"],
            "pending": copy.deepcopy(state["pending"]),
            "queue_count": len(state["queue"]),
            **result,
        }
        if digest(state) != entry["digest"] or expected_result != entry["result"]:
            raise LoreKitError(f"Alien replay divergence at command {index}")
    if state != record["state"]:
        raise LoreKitError("Alien replay final state mismatch")


def validate_snapshot(state: dict) -> None:
    """Validate persisted boundaries without accepting coercions from edited JSON."""
    if not isinstance(state, dict) or set(state) != set(initial()):
        raise LoreKitError("Invalid Alien snapshot fields")
    if state["profile"] != PROFILE or state["version"] != VERSION:
        raise LoreKitError("Incompatible Alien rules profile/version")
    integer(state["revision"], "snapshot revision")
    if state["mode"] not in ("social", "stealth", "combat") or not isinstance(state["actors"], dict):
        raise LoreKitError("Invalid Alien mode/actors")
    if not isinstance(state["queue"], list) or any(not isinstance(entry, dict) for entry in state["queue"]):
        raise LoreKitError("Invalid Alien effect queue")
    if state["pending"] is not None and not isinstance(state["pending"], dict):
        raise LoreKitError("Invalid Alien pending decision")
    if set(state["clocks"]) != set(initial()["clocks"]) or set(state["options"]) != set(initial()["options"]):
        raise LoreKitError("Invalid Alien clocks/options")
    for key, value in state["clocks"].items():
        integer(value, f"clock {key}")
    if any(type(value) is not bool for value in state["options"].values()):
        raise LoreKitError("Invalid Alien option flag")
    for a in state["actors"].values():
        if (
            not isinstance(a, dict)
            or a.get("species") not in ("human", "synthetic", "xenomorph")
            or a.get("kind") not in ("pc", "npc")
        ):
            raise LoreKitError("Invalid Alien actor")
        integer(a.get("health_max"), "maximum Health", 1)
        integer(a.get("health"), "current Health", 0, a["health_max"])
        integer(a.get("stress"), "stress")
        integer(a.get("story_points"), "story points", 0, 3)
        if (a["species"] != "human" or a["kind"] != "pc") and a["stress"]:
            raise LoreKitError("Non-human/NPC stress invariant violated")
        for name in ("dead", "hidden", "fatigued", "revealed", "secret_identity"):
            if type(a.get(name)) is not bool:
                raise LoreKitError("Invalid Alien actor flag")
