"""Nat20 (dnd5e-engine) as LoreKit's D&D 5.5e combat backend, persisted as an intent log.

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
    """Hash everything that decides future outcomes or that the GM relies on."""
    v = get_live(handle)
    summary = {
        "round": v.round_number,
        "turn": v.current_turn_index,
        "ended": v.ended,
        "hp": dict(sorted(v.tracked_hp.items())),
        "temp_hp": dict(sorted(v.tracked_temp_hp.items())),
        "dead": sorted(v.dead_ids),
        "conditions": {k: sorted(s) for k, s in sorted(v.active_conditions.items())},
        "zones": dict(sorted(v.actor_zone.items())),
        "slots": {k: {str(lv): n for lv, n in sorted(d.items())} for k, d in sorted(v.spell_slots_by_entity.items())},
        "pact": {k: {str(lv): n for lv, n in sorted(d.items())} for k, d in sorted(v.pact_slots_by_entity.items())},
        "concentration": {k: [list(t) for t in c] for k, c in sorted(v.concentration_chain.items())},
        "attacks_remaining": v.turn.attacks_remaining,
        "legendary_actions": dict(sorted(v.legendary_actions_by_entity.items())),
        "legendary_resistances": dict(sorted(v.legendary_resistances_by_entity.items())),
        "monster_uses": repr(sorted(v.monster_action_uses_by_entity.items())),
        "counters": repr(sorted(v.custom_counters_by_entity.items())),
    }
    return hashlib.sha256(json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_spec(build: dict):
    """Turn a stored (JSON) build dict into Nat20's CharacterBuildSpec."""
    b = dict(build)
    for key in ("equipment", "selected_choices", "attuned_items"):
        if key in b and b[key] is not None:
            b[key] = tuple(b[key])
    return make_build_spec(**b)


async def _open(start: dict):
    loader = BundledAssetLoader()
    party = []
    for p in start["party"]:
        inst = dict(p["instance"], zone_id=cell_id(*p["instance"]["zone_id"]))
        party.append(build_party_member(build_spec(p["build"]), CombatInstance(**inst), loader=loader))
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


async def _apply(handle, entry: dict) -> tuple[list[dict], str | None]:
    """Apply one logged call. Returns (events, error). An engine error is data, not a crash.

    A call Nat20 rejects may already have consumed RNG or emitted events, so it
    stays in the log and replay must reproduce the same error.
    """
    error = None
    try:
        if entry["op"] == "monster":
            await advance_monster_turn(handle)
        else:
            await submit_player_intent(handle, entry["actor"], PlayerIntent.model_validate(entry["intent"]))
    except Exception as exc:  # noqa: BLE001 - engine errors are recorded, not raised
        error = f"{type(exc).__name__}: {exc}"
    return [e.model_dump(mode="json") for e in drain_pending_events(handle)], error


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
            _, error = await _apply(handle, entry)
            if error != entry.get("error") or _digest(handle) != rec["digests"][i]:
                raise ReplayDivergence(f"state differs after call {i}")
        return handle

    handle = _run(rebuild())
    _CACHE[session_id] = (handle, n, want)
    return handle, rec


def _validate(handle, actor: str | None, intent: dict | None, agent_ids: frozenset = frozenset()) -> dict:
    """Reject calls that can't be right before they touch Nat20 or the log."""
    v = get_live(handle)
    if v.ended:
        raise ValueError("combat has ended")
    current = v.initiative[v.current_turn_index].entity_id
    if intent is None:
        if current in v.party_ids:
            raise ValueError(f"it is {current}'s turn; submit an intent for them")
        return {"op": "monster"}
    if actor not in v.party_ids and actor not in agent_ids:
        raise ValueError(
            f"{actor!r} cannot be given intents; party is {sorted(v.party_ids)}, agent-driven: {sorted(agent_ids)}"
        )
    try:
        parsed = PlayerIntent(**intent)
    except Exception as exc:  # pydantic ValidationError
        raise ValueError(f"invalid intent: {exc}") from exc
    if actor != current and parsed.intent_type != "reaction":
        raise ValueError(f"it is {current}'s turn, not {actor}'s")
    return {"op": "intent", "actor": actor, "intent": parsed.model_dump(mode="json")}


def start(db, session_id: int, start_spec: dict) -> dict:
    if _load(db, session_id) is not None:
        raise ValueError("a 5e combat is already active in this session; end it first")
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


def act(db, session_id: int, actor: str | None = None, intent: dict | None = None) -> dict:
    """Submit a PC intent (actor + intent) or, with neither, advance a monster turn.

    Returns {"events": [...], "error": str | None}. ValueError means the call was
    rejected before reaching Nat20 and nothing was logged; ``error`` means Nat20
    rejected it after we sent it, and the call is logged so replay stays in step.
    """
    handle, rec = _live(db, session_id)
    agent_ids = frozenset(m["entity_id"] for m in rec["start"]["encounter"] if m.get("agent_driven"))
    entry = _validate(handle, actor, intent, agent_ids)
    events, error = _run(_apply(handle, entry))
    if error:
        entry["error"] = error
    rec["log"].append(entry)
    rec["digests"].append(_digest(handle))
    _store(db, session_id, rec)
    _CACHE[session_id] = (handle, len(rec["log"]), rec["digests"][-1])
    return {"events": events, "error": error}


def status(db, session_id: int) -> dict:
    handle, rec = _live(db, session_id)
    v = get_live(handle)
    return {
        "round": v.round_number,
        "current": v.initiative[v.current_turn_index].entity_id,
        "order": [c.entity_id for c in v.initiative],
        "hp": dict(v.tracked_hp),
        "temp_hp": {k: n for k, n in v.tracked_temp_hp.items() if n},
        "dead": sorted(v.dead_ids),
        "conditions": {k: sorted(s) for k, s in v.active_conditions.items() if s},
        "zones": dict(v.actor_zone),
        "slots": {k: dict(d) for k, d in v.spell_slots_by_entity.items()},
        "concentration": {k: [list(t) for t in c] for k, c in v.concentration_chain.items()},
        "attacks_remaining": v.turn.attacks_remaining,
        "ended": v.ended,
        "calls": len(rec["log"]),
    }


def end(db, session_id: int) -> dict:
    """Close the combat and clear the log.

    Returns {"outcome": ..., "final": ...}; ``final`` is the end-of-combat state of every
    combatant, which the caller writes back to character rows.
    """
    handle, rec = _live(db, session_id)
    result = _run(end_combat(handle))
    v = get_live(handle)
    final = {
        "hp": dict(v.tracked_hp),
        "temp_hp": dict(v.tracked_temp_hp),
        "dead": sorted(v.dead_ids),
        "slots": {k: {str(lv): n for lv, n in d.items()} for k, d in v.spell_slots_by_entity.items()},
        "pact_slots": {k: {str(lv): n for lv, n in d.items()} for k, d in v.pact_slots_by_entity.items()},
        "rounds": v.round_number,
    }
    _store(db, session_id, None)
    _CACHE.pop(session_id, None)
    return {"outcome": result.outcome.model_dump(mode="json"), "final": final, "start": rec["start"]}


def active_sessions(db) -> list[tuple[int, str, int]]:
    """(session_id, engine_version, calls) for every session with a stored combat."""
    out = []
    for sid, value in db.execute("SELECT session_id, value FROM session_meta WHERE key = ?", (META_KEY,)):
        rec = json.loads(value)
        out.append((sid, rec["engine_version"], len(rec["log"])))
    return out


if __name__ == "__main__":  # python -m lorekit.backends.nat20  (check before upgrading dnd5e-engine)
    from lorekit.db import require_db

    _db = require_db()
    rows = active_sessions(_db)
    print(f"installed dnd5e-engine {ENGINE_VERSION}; {len(rows)} active combat(s)")
    for sid, ver, calls in rows:
        print(
            f"  session {sid}: engine {ver}, {calls} calls{'' if ver == ENGINE_VERSION else '  <-- version mismatch'}"
        )
