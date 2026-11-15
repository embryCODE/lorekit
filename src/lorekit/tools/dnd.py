"""MCP tools for backend-resolved 5e (dnd55e) sessions. Cruncher tools refuse these sessions."""

import json

from lorekit._mcp_app import mcp
from lorekit.backends import backend_only
from lorekit.db import LoreKitError, require_db


def _json(raw: str, what: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LoreKitError(f"{what} must be valid JSON: {e}") from e


@mcp.tool()
@backend_only
def dnd_character_create(session_id: int, name: str, build: str, type: str = "pc", region: int = 0) -> str:
    """Create a D&D 5.5e (SRD 5.2.1) character from a build spec; the build is validated first.

    build: JSON object, e.g. {"species_slug":"human","class_slug":"fighter","level":3,
      "ability_scores":{"str":16,"dex":12,"con":14,"int":10,"wis":12,"cha":8}}.
      Optional: subclass_slug (from level 3), background_slug, equipment, selected_choices,
      classes (multiclass, e.g. {"fighter":2,"wizard":1} instead of class_slug+level).
    Returns the character id plus derived HP, AC, speed, spell slots and Hit Dice.
    """
    from lorekit.dnd55e.character import create

    db = require_db()
    try:
        return json.dumps(create(db, session_id, name, _json(build, "build"), type, region), sort_keys=True)
    except LoreKitError as e:
        return f"ERROR: {e}"
    finally:
        db.close()


@mcp.tool()
@backend_only
def dnd_character_level(character_id: int, changes: str) -> str:
    """Level up or otherwise change a 5e character's build, then re-derive its sheet.

    changes: JSON object of build fields to replace, e.g. {"level":4,"ability_scores":{...}}
      or {"classes":{"fighter":3,"wizard":1}}. Current HP, slots and Hit Dice rise by what
      was gained and never exceed the new maximums.
    """
    from lorekit.dnd55e.character import level

    db = require_db()
    try:
        return json.dumps(level(db, character_id, _json(changes, "changes")), sort_keys=True)
    except LoreKitError as e:
        return f"ERROR: {e}"
    finally:
        db.close()
