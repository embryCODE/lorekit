"""Spike: can LoreKit persist a Nat20 combat as an append-only intent log?

Nat20 keeps live combat state in an in-memory, process-global registry with no
save/restore. If a combat is fully determined by (start spec, seed, ordered
calls), LoreKit can store only that log and rebuild the live combat on demand,
so there is one source of truth instead of two mirrored state models.

  uv run python spike/nat20/replay_spike.py record <log.json>
  uv run python spike/nat20/replay_spike.py replay <log.json> [--upto N]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys

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
from dnd5e_engine.spatial import parse_cell
from dnd5e_srd_data import BundledAssetLoader

START = {
    "seed": 7,
    "grid": [12, 12],
    "party": [
        {
            "build": {
                "species_slug": "human",
                "class_slug": "fighter",
                "level": 3,
                "ability_scores": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
            },
            "instance": {
                "entity_id": "char:valeros",
                "name": "Valeros",
                "hp_current": 28,
                "hp_max": 28,
                "ac": 16,
                "initiative": 12,
                "zone_id": [0, 0],
            },
            "weapon": "longsword",
        },
        {
            "build": {
                "species_slug": "elf-high",
                "class_slug": "wizard",
                "level": 3,
                "ability_scores": {"str": 8, "dex": 14, "con": 13, "int": 16, "wis": 12, "cha": 10},
            },
            "instance": {
                "entity_id": "char:ezren",
                "name": "Ezren",
                "hp_current": 17,
                "hp_max": 17,
                "ac": 12,
                "initiative": 15,
                "zone_id": [0, 2],
            },
            "weapon": "quarterstaff",
        },
    ],
    "encounter": [
        {"entity_id": f"mon:goblin{i}", "slug": "goblin-warrior", "hp": 7, "init": 10 - i, "zone_id": [6, i * 2]}
        for i in range(3)
    ],
}


def _party(start):
    loader = BundledAssetLoader()
    out = []
    for p in start["party"]:
        inst = dict(p["instance"])
        inst["zone_id"] = cell_id(*inst["zone_id"])
        out.append(build_party_member(make_build_spec(**p["build"]), CombatInstance(**inst), loader=loader))
    return out


async def open_combat(start):
    enc = [
        EncounterMemberSpec(
            entity_id=m["entity_id"],
            entity_type="Monster",
            name=m["entity_id"],
            monster_template_slug=m["slug"],
            initiative=m["init"],
            hp_current=m["hp"],
            hp_max=m["hp"],
            zone_id=cell_id(*m["zone_id"]),
        )
        for m in start["encounter"]
    ]
    res = await start_combat(
        session_id="spike",
        party=_party(start),
        encounter=enc,
        grid_scene=GridScene(width=start["grid"][0], height=start["grid"][1]),
        rng_seed=start["seed"],
    )
    return res.handle


async def apply(handle, entry):
    if entry["op"] == "monster":
        await advance_monster_turn(handle)
    else:
        await submit_player_intent(handle, entry["actor"], PlayerIntent.model_validate(entry["intent"]))
    return [e.model_dump(mode="json") for e in drain_pending_events(handle)]


def state_digest(handle) -> tuple[str, dict]:
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
    return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16], summary


def pc_policy(handle, actor_id, weapon):
    """Dumb PC driver: attack an adjacent living goblin, else walk toward the nearest."""
    v = get_live(handle)
    me = parse_cell(v.actor_zone[actor_id])
    foes = [f for f in v.encounter_ids if f not in v.dead_ids and v.tracked_hp.get(f, 1) > 0]
    if not foes:
        return {"intent_type": "pass"}

    def dist(f):
        fx, fy = parse_cell(v.actor_zone[f])
        return max(abs(fx - me[0]), abs(fy - me[1]))

    target = min(sorted(foes), key=dist)
    if dist(target) <= 1:
        return {"intent_type": "attack", "target_id": target, "weapon_id": weapon}
    tx, ty = parse_cell(v.actor_zone[target])
    occupied = set(v.actor_zone.values())
    steps = 6
    x, y = me
    while steps and max(abs(tx - x), abs(ty - y)) > 1:
        nx, ny = x + (tx > x) - (tx < x), y + (ty > y) - (ty < y)
        if cell_id(nx, ny) in occupied:
            break
        x, y, steps = nx, ny, steps - 1
    if (x, y) == me:
        return {"intent_type": "pass"}
    return {"intent_type": "move", "target_zone_id": cell_id(x, y)}


async def record(path):
    handle = await open_combat(START)
    drain_pending_events(handle)
    weapons = {p["instance"]["entity_id"]: p["weapon"] for p in START["party"]}
    log, events, digests = [], [], []
    moved = set()
    for _ in range(400):
        v = get_live(handle)
        if (
            v.ended
            or not [f for f in v.encounter_ids if f not in v.dead_ids]
            or not [p for p in v.party_ids if p not in v.dead_ids and v.tracked_hp.get(p, 0) > 0]
        ):
            break
        actor = v.initiative[v.current_turn_index]
        if actor.entity_id in v.party_ids:
            key = (v.round_number, actor.entity_id)
            intent = pc_policy(handle, actor.entity_id, weapons[actor.entity_id])
            if intent["intent_type"] == "move":
                if key in moved:
                    intent = {"intent_type": "pass"}
                moved.add(key)
            entry = {"op": "intent", "actor": actor.entity_id, "intent": PlayerIntent(**intent).model_dump(mode="json")}
        else:
            entry = {"op": "monster"}
        try:
            evs = await apply(handle, entry)
        except Exception as exc:  # a rejected intent must also replay identically
            entry["error"] = f"{type(exc).__name__}: {exc}"
            evs = [e.model_dump(mode="json") for e in drain_pending_events(handle)]
        log.append(entry)
        events.append(evs)
        digests.append(state_digest(handle)[0])
    final = await end_combat(handle)
    digest, summary = state_digest(handle)
    with open(path, "w") as f:
        json.dump(
            {
                "start": START,
                "log": log,
                "events": events,
                "digests": digests,
                "final_digest": digest,
                "final": summary,
                "outcome": final.outcome.model_dump(mode="json"),
            },
            f,
            indent=1,
            default=str,
        )
    n_ev = sum(len(e) for e in events)
    print(
        f"recorded {len(log)} calls, {n_ev} events, round {summary['round']}, "
        f"ended={final.outcome.ended_reason}, hp={summary['hp']}, digest={digest}"
    )


async def replay(path, upto=None):
    rec = json.load(open(path))
    handle = await open_combat(rec["start"])
    drain_pending_events(handle)
    log = rec["log"][:upto] if upto is not None else rec["log"]
    mismatches = 0
    for i, entry in enumerate(log):
        try:
            evs = await apply(handle, entry)
            err = None
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            evs = [e.model_dump(mode="json") for e in drain_pending_events(handle)]
        if json.loads(json.dumps(evs, default=str)) != rec["events"][i] or err != entry.get("error"):
            mismatches += 1
            print(f"call {i}: EVENT MISMATCH")
        if state_digest(handle)[0] != rec["digests"][i]:
            mismatches += 1
            print(f"call {i}: STATE MISMATCH")
    if upto is None:
        await end_combat(handle)
        ok = state_digest(handle)[0] == rec["final_digest"]
    else:
        ok = state_digest(handle)[0] == rec["digests"][upto - 1]
    print(f"replayed {len(log)} calls: {mismatches} mismatches, final state match={ok}")
    return mismatches == 0 and ok


if __name__ == "__main__":
    mode, path = sys.argv[1], sys.argv[2]
    upto = int(sys.argv[sys.argv.index("--upto") + 1]) if "--upto" in sys.argv else None
    if mode == "record":
        asyncio.run(record(path))
    else:
        sys.exit(0 if asyncio.run(replay(path, upto)) else 1)
