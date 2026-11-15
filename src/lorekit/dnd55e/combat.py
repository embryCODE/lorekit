"""5e combat orchestration: LoreKit rows <-> Nat20 start spec / write-back, monster auto-advance."""

from __future__ import annotations

import random
import string

from dnd5e_srd_data import BundledAssetLoader

from lorekit.backends import nat20
from lorekit.db import LoreKitError
from lorekit.dnd55e import character as chars

MAX_AUTO_MONSTER_TURNS = 40


def _cell(raw, what: str) -> list[int]:
    try:
        col, row = (int(x) for x in str(raw).split(","))
    except ValueError as exc:
        raise LoreKitError(f"{what}: cell must look like '4,3', got {raw!r}") from exc
    return [col, row]


def _party_rows(db, session_id: int, party) -> list[tuple[int, str]]:
    from lorekit.tools._helpers import _resolve_character

    if party:
        ids = [_resolve_character(db, p, session_id) for p in party]
    else:
        ids = [
            r[0]
            for r in db.execute(
                "SELECT id FROM characters WHERE session_id = ? AND type = 'pc' AND status = 'alive' ORDER BY id",
                (session_id,),
            )
        ]
    out = []
    for cid in ids:
        row = db.execute("SELECT name FROM characters WHERE id = ? AND session_id = ?", (cid, session_id)).fetchone()
        if row is None:
            raise LoreKitError(f"character {cid} is not in session {session_id}")
        out.append((cid, row[0]))
    if not out:
        raise LoreKitError("no living player characters to put in the combat")
    return out


def build_start(
    db, session_id: int, monsters: list[dict], grid: list[int], party=None, cells: dict | None = None, seed: int = 0
) -> dict:
    """Assemble the Nat20 start spec from character rows plus the GM's monster list."""
    if not monsters:
        raise LoreKitError("monsters must list at least one monster")
    w, h = grid
    seed = seed or random.SystemRandom().randrange(1, 2**31)
    init_rng = random.Random(seed ^ 0x5EED)
    loader = BundledAssetLoader()
    cells = cells or {}

    members = []
    rows = _party_rows(db, session_id, party)
    for i, (cid, name) in enumerate(rows):
        st = chars.read(db, cid)
        if "build_spec" not in st:
            raise LoreKitError(f"{name} has no 5e build; create them with dnd_character_create")
        if st["hp_current"] <= 0:
            raise LoreKitError(f"{name} is at 0 HP; heal or rest before starting a combat")
        cell = _cell(cells.get(str(cid)) or cells.get(name) or f"0,{min(h - 1, i * 2)}", name)
        dex = st["ability_modifiers"].get("dexterity", 0)
        inst = {
            "entity_id": f"char:{cid}",
            "name": name,
            "hp_current": st["hp_current"],
            "hp_max": st["hp_max"],
            "ac": st["ac"],
            "initiative": init_rng.randint(1, 20) + dex,
            "zone_id": cell,
            "spell_slots": {int(k): v for k, v in st["slots"].items()},
            "pact_slots": {int(k): v for k, v in st["pact_slots"].items()},
        }
        members.append({"build": st["build_spec"], "instance": inst})

    encounter = []
    counts: dict[str, int] = {}
    for i, m in enumerate(monsters):
        slug = m.get("slug")
        tmpl = loader.get_monster(slug) if slug else None
        if tmpl is None:
            words = [w for w in str(slug or "").split("-") if len(w) > 2]
            near = [s for s in loader.list_slugs("monsters") if any(w in s for w in words)][:8]
            raise LoreKitError(f"unknown monster slug {slug!r}; close matches: {near}")
        n = counts[slug] = counts.get(slug, 0) + 1
        letter = string.ascii_uppercase[(n - 1) % 26]
        dex = (tmpl.ability_scores.dex - 10) // 2
        hp = int(m.get("hp") or tmpl.hp)
        encounter.append(
            {
                "entity_id": f"mon:{slug}-{n}",
                "name": m.get("name") or f"{tmpl.name} {letter}",
                "slug": slug,
                "hp": hp,
                "init": init_rng.randint(1, 20) + dex,
                "agent_driven": bool(m.get("agent")),
                "zone_id": _cell(m.get("cell") or f"{w - 1},{min(h - 1, i * 2)}", f"monster {i}"),
            }
        )
    return {"seed": seed, "grid": [w, h], "party": members, "encounter": encounter}


def start(db, session_id: int, monsters, grid, party=None, cells=None, seed: int = 0) -> dict:
    spec = build_start(db, session_id, monsters, grid, party, cells, seed)
    nat20.start(db, session_id, spec)
    return _view(db, session_id)


def _names(rec_start: dict) -> dict[str, str]:
    names = {p["instance"]["entity_id"]: p["instance"]["name"] for p in rec_start["party"]}
    names.update({m["entity_id"]: m["name"] for m in rec_start["encounter"]})
    return names


def _view(db, session_id: int) -> dict:
    st = nat20.status(db, session_id)
    rec = nat20._load(db, session_id)
    names = _names(rec["start"])
    st["names"] = names
    st["current_name"] = names.get(st["current"], st["current"])
    st["party"] = [p["instance"]["entity_id"] for p in rec["start"]["party"]]
    agents = [m["entity_id"] for m in rec["start"]["encounter"] if m.get("agent_driven")]
    st["agent_driven"] = agents
    if st["current"] in agents and not st["ended"]:
        st["awaiting_agent"] = st["current"]
    return st


def status(db, session_id: int) -> dict:
    return _view(db, session_id)


def act(db, session_id: int, actor: str | None, intent: dict | None, run_monsters: bool = True) -> dict:
    """Apply one PC intent (or one monster turn), then optionally let monsters act until a PC is up."""
    rec = nat20._load(db, session_id)
    agent_ids = {m["entity_id"] for m in rec["start"]["encounter"] if m.get("agent_driven")}
    steps = [{"actor": actor or "monster", **nat20.act(db, session_id, actor, intent)}]
    if run_monsters and steps[0]["error"] is None:
        for _ in range(MAX_AUTO_MONSTER_TURNS):
            st = nat20.status(db, session_id)
            if st["ended"] or st["current"].startswith("char:") or st["current"] in agent_ids:
                break
            res = nat20.act(db, session_id)
            steps.append({"actor": st["current"], **res})
            if res["error"]:
                break
    out = _view(db, session_id)
    out["steps"] = steps
    return out


def end(db, session_id: int) -> dict:
    """Close the combat and write PC results back to their rows in one transaction."""
    res = nat20.end(db, session_id)
    final, start_spec = res["final"], res["start"]
    died = set(final["dead"])
    for p in start_spec["party"]:
        eid = p["instance"]["entity_id"]
        cid = int(eid.split(":")[1])
        chars.write(
            db,
            cid,
            hp_current=max(0, final["hp"].get(eid, 0)),
            hp_temp=final["temp_hp"].get(eid, 0),
            slots=final["slots"].get(eid, {}),
            pact_slots=final["pact_slots"].get(eid, {}),
        )
        if eid in died:
            db.execute("UPDATE characters SET status = 'dead' WHERE id = ?", (cid,))
    db.commit()
    names = _names(start_spec)
    return {
        "ended_reason": res["outcome"]["ended_reason"],
        "rounds": final["rounds"],
        "deaths": [names.get(d["target_id"], d["target_id"]) for d in res["outcome"]["deaths"]],
        "party": {
            names[p["instance"]["entity_id"]]: {
                "hp": f"{max(0, final['hp'].get(p['instance']['entity_id'], 0))}/{p['instance']['hp_max']}",
            }
            for p in start_spec["party"]
        },
        "xp_awarded": res["outcome"].get("xp_awarded"),
    }
