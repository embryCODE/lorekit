"""5e character creation and state, stored as LoreKit character attributes.

LoreKit owns a character's state outside combat. Nat20 receives it once when a
combat starts and hands it back once when the combat ends (see
``dnd_combat_start`` / ``dnd_combat_end``). The build spec is kept so the sheet
can be re-derived whenever the character levels up.
"""

from __future__ import annotations

import json

from dnd5e_engine import derive_sheet
from dnd5e_srd_data import BundledAssetLoader

from lorekit.backends.nat20 import build_spec
from lorekit.db import LoreKitError

CATEGORY = "dnd55e"

# Keys holding JSON; everything else is a plain scalar string.
_JSON_KEYS = {
    "build_spec",
    "hit_dice",
    "hit_dice_total",
    "slots",
    "slots_max",
    "pact_slots",
    "pact_slots_max",
    "conditions",
    "ability_modifiers",
}


def _loader():
    return BundledAssetLoader()


def derive(build: dict):
    """Validate a build through Nat20 and return its derived sheet. Raises LoreKitError."""
    try:
        return derive_sheet(build_spec(build), loader=_loader())
    except (ValueError, TypeError, KeyError) as exc:
        raise LoreKitError(f"invalid 5e build: {exc}") from exc


def _levels(build: dict) -> int:
    if build.get("classes"):
        return sum(build["classes"].values())
    return int(build.get("level") or 1)


def _enc(key: str, value) -> str:
    return json.dumps(value, sort_keys=True) if key in _JSON_KEYS else str(value)


def _dec(key: str, raw: str):
    if key in _JSON_KEYS:
        return json.loads(raw)
    if key == "heroic_inspiration":
        return raw == "1"
    try:
        return int(raw)
    except ValueError:
        return raw


def _int_keys(d: dict) -> dict:
    return {int(k): v for k, v in d.items()}


def read(db, character_id: int) -> dict:
    """All 5e attributes of a character, decoded. Empty dict if it isn't a 5e character."""
    rows = db.execute(
        "SELECT key, value FROM character_attributes WHERE character_id = ? AND category = ?",
        (character_id, CATEGORY),
    ).fetchall()
    return {k: _dec(k, v) for k, v in rows}


def write(db, character_id: int, **fields) -> None:
    from lorekit.queries import upsert_attribute

    for key, value in fields.items():
        upsert_attribute(db, character_id, CATEGORY, key, _enc(key, value))


def _sheet_fields(sheet) -> dict:
    return {
        "hp_max": sheet.hp_max,
        "ac": sheet.ac,
        "speed": sheet.base_speed,
        "proficiency_bonus": sheet.proficiency_bonus,
        "passive_perception": sheet.passive_perception,
        "ability_modifiers": dict(sheet.ability_modifiers),
        "slots_max": {str(k): v for k, v in sheet.spell_slots.items()},
        "pact_slots_max": {str(k): v for k, v in sheet.pact_slots.items()},
        "hit_dice_total": {str(k): v for k, v in sheet.hit_dice.items()},
    }


def create(db, session_id: int, name: str, build: dict, char_type: str = "pc", region_id: int = 0) -> dict:
    """Validate ``build`` through Nat20, then create the character with full starting state."""
    from lorekit.backends import session_backend
    from lorekit.character import create as create_row

    be = session_backend(db, session_id)
    if be is None or be.name != "nat20":
        raise LoreKitError(f"session {session_id} is not a dnd55e session")
    sheet = derive(build)  # raises before any row is written
    out = create_row(db, session_id, name, _levels(build), char_type, region_id)
    cid = int(out.split(": ")[1])
    fields = _sheet_fields(sheet)
    write(
        db,
        cid,
        build_spec=build,
        hp_current=sheet.hp_max,
        hp_temp=0,
        slots=fields["slots_max"],
        pact_slots=fields["pact_slots_max"],
        hit_dice=fields["hit_dice_total"],
        exhaustion=0,
        heroic_inspiration=0,
        conditions=[],
        **fields,
    )
    db.commit()
    return {"character_id": cid, **read(db, cid)}


def level(db, character_id: int, changes: dict) -> dict:
    """Apply ``changes`` to the stored build, re-derive the sheet, and keep current state sensible.

    Gained HP, spell slots and Hit Dice are added to the current values (clamped to the
    new maximums); nothing is ever left above its maximum.
    """
    st = read(db, character_id)
    if "build_spec" not in st:
        raise LoreKitError(f"character {character_id} has no 5e build")
    new_build = {**st["build_spec"], **changes}
    sheet = derive(new_build)
    f = _sheet_fields(sheet)

    def grown(cur: dict, old_max: dict, new_max: dict) -> dict:
        out = {}
        for k, mx in new_max.items():
            gain = max(0, mx - old_max.get(k, 0))
            out[k] = min(mx, cur.get(k, 0) + gain)
        return out

    hp_gain = max(0, f["hp_max"] - st["hp_max"])
    write(
        db,
        character_id,
        build_spec=new_build,
        hp_current=min(f["hp_max"], st["hp_current"] + hp_gain),
        slots=grown(st["slots"], st["slots_max"], f["slots_max"]),
        pact_slots=grown(st["pact_slots"], st["pact_slots_max"], f["pact_slots_max"]),
        hit_dice=grown(st["hit_dice"], st["hit_dice_total"], f["hit_dice_total"]),
        **f,
    )
    db.execute("UPDATE characters SET level = ? WHERE id = ?", (_levels(new_build), character_id))
    db.commit()
    return {"character_id": character_id, **read(db, character_id)}


def combat_instance_fields(state: dict) -> dict:
    """The slice of stored state Nat20's CombatInstance needs at combat start."""
    return {"hp_current": state["hp_current"], "hp_max": state["hp_max"], "ac": state["ac"]}
