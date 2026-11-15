"""Out-of-combat 5e rules: ability checks, saves and rests, resolved by Nat20.

Every roll uses ``random.Random(seed)`` with the seed returned to the caller and
written to the journal, so a roll can be audited and reproduced. Results go
straight to the character rows; nothing here is replayed.
"""

from __future__ import annotations

import random

from dnd5e_engine import CheckSpec, HitDicePool, resolve_check, resolve_long_rest, resolve_short_rest

from lorekit.db import LoreKitError
from lorekit.dnd55e import character as chars
from lorekit.narrative import journal

ABILITIES = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}


def _ability(name: str) -> str:
    n = name.strip().lower()
    n = ABILITIES.get(n, n)
    if n not in ABILITIES.values():
        raise LoreKitError(f"unknown ability {name!r}; use one of {sorted(ABILITIES.values())}")
    return n


def _seed(seed: int) -> int:
    return seed or random.SystemRandom().randrange(1, 2**31)


def _char_row(db, character_id: int):
    row = db.execute("SELECT session_id, name, status FROM characters WHERE id = ?", (character_id,)).fetchone()
    if row is None:
        raise LoreKitError(f"character {character_id} not found")
    return row


def check(
    db,
    character_id: int,
    kind: str,
    name: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int = 0,
) -> dict:
    """Roll a skill check, ability check or saving throw for a character against a DC."""
    session_id, who, _ = _char_row(db, character_id)
    st = chars.read(db, character_id)
    if "build_spec" not in st:
        raise LoreKitError(f"{who} has no 5e build")
    sheet = chars.derive(st["build_spec"])
    scores = {full: getattr(sheet.ability_scores, full) for full in ABILITIES.values()}
    saves = tuple(sorted({*sheet.save_proficiencies, *(ABILITIES.get(s, s) for s in sheet.save_proficiencies)}))
    if kind not in ("skill", "ability", "saving_throw"):
        raise LoreKitError("kind must be skill, ability or saving_throw")
    seed = _seed(seed)
    spec = CheckSpec(
        kind=kind,
        ability_scores=scores,
        proficient_skills=tuple(sorted(sheet.skill_proficiencies)),
        proficient_saves=saves,
        proficiency_bonus=sheet.proficiency_bonus,
        skill=name.strip().lower().replace(" ", "_").replace("-", "_") if kind == "skill" else None,
        ability=_ability(name) if kind != "skill" else None,
        dc=dc,
        advantage=advantage,
        disadvantage=disadvantage,
        expertise_skills=tuple(sorted(sheet.skill_expertise)),
        jack_of_all_trades=sheet.jack_of_all_trades,
        reliable_talent=sheet.reliable_talent,
        rng=random.Random(seed),
    )
    try:
        res = resolve_check(spec)
    except ValueError as exc:
        raise LoreKitError(str(exc)) from exc
    out = {
        "character": who,
        "kind": kind,
        "name": name,
        "dc": dc,
        "natural_roll": res.natural_roll,
        "modifier": res.modifier,
        "total": res.roll_total,
        "success": res.success,
        "proficient": res.is_proficient,
        "advantage": advantage and not disadvantage,
        "disadvantage": disadvantage and not advantage,
        "seed": seed,
    }
    verdict = "success" if res.success else "failure"
    journal.add(
        db,
        session_id,
        "check",
        f"{who}: {kind} {name} vs DC {dc} -> d20 {res.natural_roll} {res.modifier:+d} = {res.roll_total} ({verdict}); seed {seed}",
        scope="gm",
    )
    return out


def _pools(st: dict, classes_hit_dice: dict) -> list[HitDicePool]:
    return [
        HitDicePool(hit_die_size=int(size), dice_remaining=st["hit_dice"].get(size, 0), dice_total=total)
        for size, total in sorted(classes_hit_dice.items(), key=lambda kv: -int(kv[0]))
    ]


def short_rest(db, character_id: int, spend: int = 0, seed: int = 0) -> dict:
    """Spend up to ``spend`` Hit Dice (largest first) to heal; warlock pact slots return."""
    session_id, who, status = _char_row(db, character_id)
    st = chars.read(db, character_id)
    seed = _seed(seed)
    rng = random.Random(seed)
    con = st["ability_modifiers"].get("constitution", 0)
    hp, remaining, rolls, healed = st["hp_current"], dict(st["hit_dice"]), [], 0
    pact = {int(k): v for k, v in st["pact_slots"].items()}
    pact_max = {int(k): v for k, v in st["pact_slots_max"].items()}
    left = spend
    for pool in _pools(st, st["hit_dice_total"]):
        n = min(left, pool.dice_remaining)
        res = resolve_short_rest(pool, n, con, rng=rng, pact_slots=pact or None, pact_slot_max=pact_max or None)
        healed += res.healed
        rolls += list(res.rolls)
        remaining[str(pool.hit_die_size)] = res.dice_remaining
        left -= res.dice_spent
        if res.pact_slots is not None:
            pact = dict(res.pact_slots)
    if spend and left == spend:
        raise LoreKitError(f"{who} has no Hit Dice left to spend")
    new_hp = min(st["hp_max"], hp + healed)
    chars.write(
        db,
        character_id,
        hp_current=new_hp,
        hit_dice=remaining,
        pact_slots={str(k): v for k, v in pact.items()},
    )
    db.commit()
    out = {
        "character": who,
        "dice_spent": spend - left,
        "rolls": rolls,
        "healed": healed,
        "hp": f"{new_hp}/{st['hp_max']}",
        "hit_dice_remaining": remaining,
        "seed": seed,
    }
    journal.add(
        db,
        session_id,
        "rest",
        f"{who}: short rest, {out['dice_spent']} Hit Dice, healed {healed}; seed {seed}",
        scope="gm",
    )
    return out


def long_rest(db, character_id: int) -> dict:
    """Restore HP, slots and Hit Dice per the 2024 rules; exhaustion drops by one."""
    session_id, who, status = _char_row(db, character_id)
    st = chars.read(db, character_id)
    if status == "dead":
        raise LoreKitError(f"{who} is dead; a rest cannot help")
    total = st["hit_dice_total"]
    pool = _pools(st, total)[0]
    res = resolve_long_rest(
        pool,
        st["hp_current"],
        st["hp_max"],
        spell_slots={int(k): v for k, v in st["slots"].items()},
        spell_slot_max={int(k): v for k, v in st["slots_max"].items()},
        pact_slots={int(k): v for k, v in st["pact_slots"].items()},
        pact_slot_max={int(k): v for k, v in st["pact_slots_max"].items()},
        exhaustion_level=st["exhaustion"],
    )
    # Long rest regains spent Hit Dice up to half the total (min 1); the engine restores the
    # pool it was handed, so work it out per die size here.
    hit_dice = {}
    for size, tot in total.items():
        cur = st["hit_dice"].get(size, 0)
        hit_dice[size] = min(tot, cur + max(1, tot // 2))
    chars.write(
        db,
        character_id,
        hp_current=res.hp_current,
        hp_temp=0,
        slots={str(k): v for k, v in (res.spell_slots or {}).items()},
        pact_slots={str(k): v for k, v in (res.pact_slots or {}).items()},
        hit_dice=hit_dice,
        exhaustion=res.exhaustion_level if res.exhaustion_level is not None else st["exhaustion"],
    )
    db.commit()
    out = {
        "character": who,
        "hp": f"{res.hp_current}/{st['hp_max']}",
        "slots": {str(k): v for k, v in (res.spell_slots or {}).items()},
        "hit_dice": hit_dice,
        "exhaustion": res.exhaustion_level,
    }
    journal.add(db, session_id, "rest", f"{who}: long rest; HP {out['hp']}", scope="gm")
    return out
