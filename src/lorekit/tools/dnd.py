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
      Optional: subclass_slug (from level 3), background_slug, equipment (list of SRD item slugs,
      e.g. ["chain-mail","longsword","shield"]; without armor AC is 10 + Dex), selected_choices,
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


def _combat_call(fn, *args, **kwargs) -> str:
    db = require_db()
    try:
        return json.dumps(fn(db, *args, **kwargs), sort_keys=True, default=str)
    except (LoreKitError, ValueError) as e:
        return f"ERROR: {e}"
    except Exception as e:  # ReplayDivergence and engine faults must not crash the MCP server
        return f"ERROR: {type(e).__name__}: {e}"
    finally:
        db.close()


@mcp.tool()
@backend_only
def dnd_combat_start(
    session_id: int,
    monsters: str,
    grid: str = "12x12",
    party: str = "[]",
    cells: str = "{}",
    seed: int = 0,
) -> str:
    """Start a 5e combat on a grid. Positions are cells written "col,row" (0-based).

    monsters: JSON list of {"slug":"goblin-warrior","cell":"6,2","name":"optional","hp":optional,"agent":false}.
      "agent": true marks an NPC you will drive yourself (e.g. via its NPC agent): monster turns stop
      when it is that NPC's turn ("awaiting_agent" in the result), and you submit its intent with
      dnd_combat_act(actor="mon:...", intent=...). Calling dnd_combat_act with no intent on that turn
      falls back to the built-in monster AI.
      Slugs are SRD 5.2.1 monster slugs. Without "cell" monsters line up on the far column.
    grid: "WxH", e.g. "12x12" (one cell is 5 ft).
    party: JSON list of character ids or names; empty means every living PC in the session.
    cells: JSON {"<character id or name>": "col,row"} for starting positions (default: left column).
    seed: optional; a random seed is chosen and recorded when 0. Initiative is rolled for you.
    Returns the initiative order, positions and whose turn it is.
    """
    from lorekit.dnd55e.combat import start

    try:
        w, h = (int(x) for x in grid.lower().split("x"))
        return _combat_call(
            start,
            session_id,
            _json(monsters, "monsters"),
            [w, h],
            _json(party, "party") or None,
            _json(cells, "cells"),
            seed,
        )
    except (LoreKitError, ValueError) as e:
        return f"ERROR: {e}"


@mcp.tool()
@backend_only
def dnd_combat_status(session_id: int) -> str:
    """Live state of the active 5e combat: round, current actor, HP, conditions, cells, slots, concentration."""
    from lorekit.dnd55e.combat import status

    return _combat_call(status, session_id)


@mcp.tool()
@backend_only
def dnd_combat_act(session_id: int, actor: str = "", intent: str = "", run_monsters: bool = True) -> str:
    """Resolve one action in the active 5e combat; every number is rolled by the rules engine.

    actor: entity id of the acting PC or agent-driven NPC, e.g. "char:3" (see dnd_combat_status "party").
    intent: JSON, e.g. {"intent_type":"attack","target_id":"mon:goblin-warrior-1","weapon_id":"longsword"}.
      intent_type: attack, cast_spell, move, dash, dodge, disengage, hide, help, ready, use_item,
      use_feature, grapple, shove, stand_up, escape_grapple, drop_concentration, pass.
      cast_spell takes spell_id, target_id(s), slot_level; move takes target_zone_id "col,row".
      Reactions (Shield, Counterspell, ...) are pre-armed on the PC's own turn with
      {"intent_type":"ready","reaction_trigger":"hit_by_attack"|"cast_spell"|"targeted_by_magic_missile", ...}.
    With no actor/intent, advances one monster turn.
    run_monsters: after a PC acts, monsters take their turns automatically until it is a PC's turn.
    Returns "steps" (each with the rolled events, or an "error") plus the new status. Narrate from the events.
    """
    from lorekit.dnd55e.combat import act

    try:
        parsed = _json(intent, "intent") if intent else None
    except LoreKitError as e:
        return f"ERROR: {e}"
    return _combat_call(act, session_id, actor or None, parsed, run_monsters)


@mcp.tool()
@backend_only
def dnd_combat_end(session_id: int) -> str:
    """End the active 5e combat and write each PC's final HP, temp HP and spell slots to their sheet.

    Characters who died are marked dead. Rests (dnd_rest) restore slots and Hit Dice afterwards.
    """
    from lorekit.dnd55e.combat import end

    return _combat_call(end, session_id)


def _char_call(fn, character_id, *args, **kwargs) -> str:
    from lorekit.tools._helpers import _resolve_character

    db = require_db()
    try:
        cid = _resolve_character(db, character_id)
        return json.dumps(fn(db, cid, *args, **kwargs), sort_keys=True, default=str)
    except (LoreKitError, ValueError) as e:
        return f"ERROR: {e}"
    finally:
        db.close()


@mcp.tool()
@backend_only
def dnd_check(
    character_id: int | str,
    kind: str,
    name: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int = 0,
) -> str:
    """Roll an out-of-combat check or save for a 5e character against a DC. The engine rolls; never invent dice.

    kind: "skill" (name e.g. "stealth", "sleight_of_hand"), "ability" (name e.g. "strength")
      or "saving_throw" (name e.g. "wisdom").
    advantage/disadvantage cancel each other if both are set. Returns the d20, modifier, total,
    success and the seed (pass the seed back to reproduce the same roll).
    """
    from lorekit.dnd55e.rules import check

    return _char_call(check, character_id, kind, name, dc, advantage, disadvantage, seed)


@mcp.tool()
@backend_only
def dnd_rest(character_id: int | str, type: str, hit_dice: int = 0, seed: int = 0) -> str:
    """Rest a 5e character and write the result to their sheet.

    type: "short" (spend `hit_dice` Hit Dice to heal; warlock pact slots return) or
      "long" (full HP, all spell slots, half the Hit Dice back, exhaustion -1).
    Call once per character who rests.
    """
    from lorekit.dnd55e.rules import long_rest, short_rest

    if type == "short":
        return _char_call(short_rest, character_id, hit_dice, seed)
    if type == "long":
        return _char_call(long_rest, character_id)
    return "ERROR: type must be short or long"
