"""SPIKE: Nat20 as LoreKit's D&D 5.5e combat backend, persisted as an intent log.

One source of truth: LoreKit stores a combat as (start spec, seed, ordered
calls) in session_meta. Nat20's in-memory combat is a disposable cache, rebuilt
by deterministic replay whenever it is missing (process restart) or out of step
with the stored log (checkpoint revert, save load, branch). Each call stores a
state digest, so a replay that diverges is detected instead of silently
continuing.

Because the log lives in session_meta, LoreKit's existing checkpoints
(turn_save / turn_revert / save_load / branching) cover it with no changes.

State outside combat (HP, slots, exhaustion) stays in LoreKit's character rows.
It is handed to Nat20 in the start spec and written back once by ``end``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
from importlib.metadata import version

from dnd5e_engine import (
    CombatInstance,
    EncounterMemberSpec,
    GridScene,
    PlayerIntent,
    advance_monster_turn,
    build_party_member,
    cell_id,
    drain_pending_events,
    end_combat,
    get_live,
    make_build_spec,
    start_combat,
    submit_player_intent,
)
from dnd5e_srd_data import BundledAssetLoader

META_KEY = "nat20_combat"
ENGINE_VERSION = version("dnd5e-engine")


class ReplayDivergence(RuntimeError):
    """The stored log no longer reproduces the stored state."""


# session_id -> (handle, calls applied, digest after last call). A cache only.
_CACHE: dict[int, tuple[object, int, str]] = {}


def _run(coro):
    """Run a Nat20 coroutine from sync LoreKit code, even inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _load(db, session_id) -> dict | None:
    row = db.execute(
        "SELECT value FROM session_meta WHERE session_id = ? AND key = ?", (session_id, META_KEY)
    ).fetchone()
    return json.loads(row[0]) if row else None


def _store(db, session_id, rec: dict | None) -> None:
    if rec is None:
        db.execute("DELETE FROM session_meta WHERE session_id = ? AND key = ?", (session_id, META_KEY))
    else:
        db.execute(
            "INSERT INTO session_meta (session_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value",
            (session_id, META_KEY, json.dumps(rec, separators=(",", ":"))),
        )
    db.commit()


def _digest(handle) -> str:
    v = get_live(handle)
    summary = {
        "round": v.round_number,
        "turn": v.current_turn_index,
        "ended": v.ended,
        "hp": dict(sorted(v.tracked_hp.items())),
        "dead": sorted(v.dead_ids),
        "conditions": {k: sorted(s) for k, s in sorted(v.active_conditions.items())},
        "zones": dict(sorted(v.actor_zone.items())),
        "slots": {k: {str(l): n for l, n in sorted(d.items())} for k, d in sorted(v.spell_slots_by_entity.items())},
    }
    return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]


async def _open(start: dict):
    loader = BundledAssetLoader()
    party = []
    for p in start["party"]:
        inst = dict(p["instance"], zone_id=cell_id(*p["instance"]["zone_id"]))
        party.append(build_party_member(make_build_spec(**p["build"]), CombatInstance(**inst), loader=loader))
    encounter = [
        EncounterMemberSpec(
            entity_id=m["entity_id"],
            entity_type="Monster",
            name=m.get("name", m["entity_id"]),
            monster_template_slug=m["slug"],
            initiative=m["init"],
            hp_current=m["hp"],
            hp_max=m["hp"],
            zone_id=cell_id(*m["zone_id"]),
        )
        for m in start["encounter"]
    ]
    res = await start_combat(
        session_id=str(start.get("session_id", "lorekit")),
        party=party,
        encounter=encounter,
        grid_scene=GridScene(width=start["grid"][0], height=start["grid"][1]),
        rng_seed=start["seed"],
    )
    drain_pending_events(res.handle)
    return res.handle


async def _apply(handle, entry: dict) -> list[dict]:
    if entry["op"] == "monster":
        await advance_monster_turn(handle)
    else:
        await submit_player_intent(handle, entry["actor"], PlayerIntent.model_validate(entry["intent"]))
    return [e.model_dump(mode="json") for e in drain_pending_events(handle)]


def _live(db, session_id):
    """Return the Nat20 handle matching the stored log, rebuilding it if needed."""
    rec = _load(db, session_id)
    if rec is None:
        raise ValueError("no active Nat20 combat in this session")
    n = len(rec["log"])
    want = rec["digests"][-1] if n else rec["start_digest"]
    cached = _CACHE.get(session_id)
    if cached and cached[1] == n and cached[2] == want:
        return cached[0], rec
    if rec["engine_version"] != ENGINE_VERSION:
        raise ReplayDivergence(f"log recorded with dnd5e-engine {rec['engine_version']}, running {ENGINE_VERSION}")

    async def rebuild():
        handle = await _open(rec["start"])
        if _digest(handle) != rec["start_digest"]:
            raise ReplayDivergence("start state differs")
        for i, entry in enumerate(rec["log"]):
            await _apply(handle, entry)
            if _digest(handle) != rec["digests"][i]:
                raise ReplayDivergence(f"state differs after call {i}")
        return handle

    handle = _run(rebuild())
    _CACHE[session_id] = (handle, n, want)
    return handle, rec


def start(db, session_id: int, start_spec: dict) -> dict:
    start_spec = dict(start_spec, session_id=session_id)
    handle = _run(_open(start_spec))
    rec = {
        "engine_version": ENGINE_VERSION,
        "start": start_spec,
        "start_digest": _digest(handle),
        "log": [],
        "digests": [],
    }
    _store(db, session_id, rec)
    _CACHE[session_id] = (handle, 0, rec["start_digest"])
    return status(db, session_id)


def act(db, session_id: int, actor: str | None = None, intent: dict | None = None) -> list[dict]:
    """Submit a PC intent (actor + intent) or, with neither, advance a monster turn."""
    handle, rec = _live(db, session_id)
    entry = (
        {"op": "monster"}
        if intent is None
        else {
            "op": "intent",
            "actor": actor,
            "intent": PlayerIntent(**intent).model_dump(mode="json"),
        }
    )
    events = _run(_apply(handle, entry))
    rec["log"].append(entry)
    rec["digests"].append(_digest(handle))
    _store(db, session_id, rec)
    _CACHE[session_id] = (handle, len(rec["log"]), rec["digests"][-1])
    return events


def status(db, session_id: int) -> dict:
    handle, rec = _live(db, session_id)
    v = get_live(handle)
    return {
        "round": v.round_number,
        "current": v.initiative[v.current_turn_index].entity_id,
        "hp": dict(v.tracked_hp),
        "dead": sorted(v.dead_ids),
        "conditions": {k: sorted(s) for k, s in v.active_conditions.items() if s},
        "zones": dict(v.actor_zone),
        "calls": len(rec["log"]),
    }


def end(db, session_id: int) -> dict:
    """Close the combat and hand results back to LoreKit's character rows (spike: HP only)."""
    handle, rec = _live(db, session_id)
    result = _run(end_combat(handle))
    outcome = result.outcome.model_dump(mode="json")
    _store(db, session_id, None)
    _CACHE.pop(session_id, None)
    return outcome
