"""GM-only Alien Evolved Beta 2 mutations and audience-filtered read tools.

Every mutation needs a stable request ID and the latest expected revision.
Use public status for narration; mutation results are explicitly GM-only.
"""

import json

from lorekit._mcp_app import mcp
from lorekit.alien import content
from lorekit.alien.state import execute, load, projection
from lorekit.backends import system_only
from lorekit.db import LoreKitError, require_db

alien_only = system_only("alien-evolved")


def _object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise LoreKitError("Expected a JSON object") from exc
    if not isinstance(value, dict):
        raise LoreKitError("Expected a JSON object")
    return value


def _mutate(session_id, intent, request_id, expected_revision, seed=None):
    db = require_db()
    try:
        return json.dumps(execute(db, session_id, intent, request_id, expected_revision, seed), sort_keys=True)
    except (LoreKitError, ValueError, TypeError) as exc:
        return f"ERROR: {exc}"
    finally:
        db.close()


@mcp.tool()
@alien_only
def alien_scene_status(session_id: int, audience: str = "public") -> str:
    """Read canonical Alien state. Public is filtered; gm explicitly reveals private state."""
    db = require_db()
    try:
        return json.dumps(projection(load(db, session_id)["state"], audience), sort_keys=True)
    except LoreKitError as exc:
        return f"ERROR: {exc}"
    finally:
        db.close()


@mcp.tool()
@alien_only
def alien_character_import(session_id: int, actor_id: str, build: str, request_id: str, expected_revision: int) -> str:
    """GM-only: import a pregenerated human/synthetic; build contains final attributes, skills and local content IDs."""
    try:
        return _mutate(
            session_id,
            {"op": "character_import", "actor": actor_id, "build": _object(build)},
            request_id,
            expected_revision,
        )
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_character_create(session_id: int, actor_id: str, build: str, request_id: str, expected_revision: int) -> str:
    """GM-only: validate standard point-buy against an imported local career and its talent options."""
    try:
        return _mutate(
            session_id,
            {"op": "character_create", "actor": actor_id, "build": _object(build)},
            request_id,
            expected_revision,
        )
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_check(session_id: int, check: str, request_id: str, expected_revision: int, seed: int | None = None) -> str:
    """GM-only: roll {actor,skill,attribute?,modifier?,helpers?,passive?,opponent?}. Returns dice and pending decision ID."""
    try:
        return _mutate(session_id, {**_object(check), "op": "check"}, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_check_decide(
    session_id: int, decision: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: decide {decision_id,actor,choice:accept|push|story_point,die?:zero-based index}."""
    try:
        return _mutate(session_id, {**_object(decision), "op": "check_decide"}, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_content_import(session_id: int, bundle: str, request_id: str, expected_revision: int) -> str:
    """GM-only: validate/install local evolved-beta2 content before actors are created. Never uploads the PDF."""
    try:
        fingerprint = content.install(_object(bundle))
        return _mutate(session_id, {"op": "content_import", "hash": fingerprint}, request_id, expected_revision)
    except (LoreKitError, ValueError) as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_stress(
    session_id: int, actor_id: str, reason: str, request_id: str, expected_revision: int, amount: int = 1
) -> str:
    """GM-only: apply a classified stress trigger; species/broken immunities are enforced."""
    return _mutate(
        session_id,
        {"op": "stress", "actor": actor_id, "reason": reason, "amount": amount},
        request_id,
        expected_revision,
    )


@mcp.tool()
@alien_only
def alien_panic(
    session_id: int, actor_id: str, reason: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: enqueue a classified panic trigger and resolve until a required choice or queue limit."""
    return _mutate(
        session_id, {"op": "panic", "actor": actor_id, "reason": reason}, request_id, expected_revision, seed
    )


def _json_command(session_id, raw, op, request_id, expected_revision, seed=None):
    try:
        return _mutate(session_id, {**_object(raw), "op": op}, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_scene_setup(session_id: int, scene: str, request_id: str, expected_revision: int) -> str:
    """GM-only: configure {zones:{id:{cluttered,dark,crawlspace}},borders:[{a,b,open,door,locked,sealed}],placements:{actor:zone}}."""
    return _json_command(session_id, scene, "scene_setup", request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_combat_start(
    session_id: int, combat: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: draw initiative for {actors:[actor IDs],groups?:[[NPC IDs]],surprise?:[actor IDs],ambush?:[actor IDs]}."""
    return _json_command(session_id, combat, "combat_start", request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_combat_status(session_id: int, audience: str = "public") -> str:
    """Read audience-filtered Alien combat status; gm reveals mechanical/private state."""
    return alien_scene_status(session_id, audience)


@mcp.tool()
@alien_only
def alien_combat_act(
    session_id: int, intent: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: op attack, defense_decide, check_decide, burst_decide, panic_decide, move, hold_off or end_turn.

    Declare attack {actor,target,weapon?:inventory ID,aim?,weak_spot?,full_auto?,conserve?}.
    Defenses require {actor,decision_id,choice:defend|dodge|decline} BEFORE dice are rolled.
    Check decisions use choice accept|push|story_point. Unimplemented actions fail explicitly.
    """
    try:
        parsed = _object(intent)
        if parsed.get("op") not in {
            "attack",
            "action",
            "creature_act",
            "creature_move",
            "leave_combat",
            "creature_escape_decide",
            "egg_release",
            "facehugger_remove",
            "signature_decide",
            "defense_decide",
            "check_decide",
            "burst_decide",
            "panic_decide",
            "move",
            "approach",
            "hold_off",
            "initiative_decide",
            "special_decide",
            "hit_decide",
            "injury_decide",
            "overkill_decide",
            "area_defense",
            "end_turn",
            "begin_turn",
            "queue_continue",
        }:
            raise LoreKitError("Unsupported combat intent")
        return _mutate(session_id, parsed, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_combat_end(session_id: int, request_id: str, expected_revision: int) -> str:
    """GM-only: end combat, retaining canonical health, supplies, injuries and effects."""
    return _mutate(session_id, {"op": "combat_end"}, request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_supply(
    session_id: int, actor_id: str, item_id: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: roll an inventory item's air/ammo/power supply, at most six dice."""
    return _mutate(
        session_id, {"op": "supply", "actor": actor_id, "item": item_id}, request_id, expected_revision, seed
    )


@mcp.tool()
@alien_only
def alien_time_advance(
    session_id: int, unit: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: advance one logical round/stretch/shift/day; resolve due effects before later actions."""
    return _mutate(session_id, {"op": "time_advance", "unit": unit}, request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_character_view(session_id: int, actor_id: str, audience: str = "public") -> str:
    """Read one actor's public facts or explicit GM-only mechanical sheet."""
    from lorekit.alien.state import actor

    db = require_db()
    try:
        state = load(db, session_id)["state"]
        if audience == "gm":
            actor(state, actor_id)
        visible = projection(state, audience)
        if actor_id not in visible["actors"]:
            raise LoreKitError("Actor not found or not public")
        return json.dumps(
            {
                "visibility": "gm_only" if audience == "gm" else "public",
                "profile": state["profile"],
                "revision": state["revision"],
                "actor": visible["actors"].get(actor_id, {}),
            },
            sort_keys=True,
        )
    except LoreKitError as exc:
        return f"ERROR: {exc}"
    finally:
        db.close()


@mcp.tool()
@alien_only
def alien_stealth_act(
    session_id: int, intent: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: op stealth_act {actor,action:move|hide|guard|search|wait,zone?,group?}, stealth_phase, tracker or detection_decide."""
    try:
        parsed = _object(intent)
        if parsed.get("op") not in {
            "stealth_act",
            "stealth_phase",
            "tracker",
            "detection_decide",
            "search_decide",
            "scene_mode",
            "check_decide",
        }:
            raise LoreKitError("Unsupported stealth operation")
        return _mutate(session_id, parsed, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_recover(
    session_id: int, recovery: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: {actor,target?,method:first_aid|rally|repair|surgery|stop_panic|rest|reactivate|disease_care,...}.

    Rest needs safe:true; repairs/surgery need shift_completed:true. Check decisions
    are handled by alien_check_decide. Resources, injuries and clocks persist.
    """
    return _json_command(session_id, recovery, "recover", request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_story_award(
    session_id: int, actor_id: str, reason: str, act_id: str, request_id: str, expected_revision: int
) -> str:
    """GM-only: award one cinematic story point for an explicit agenda-related reason; cap three."""
    return _mutate(
        session_id,
        {"op": "story_award", "actor": actor_id, "reason": reason, "act_id": act_id},
        request_id,
        expected_revision,
    )


@mcp.tool()
@alien_only
def alien_rules_override(session_id: int, ruling: str, request_id: str, expected_revision: int) -> str:
    """GM-only: audited exceptional ruling {reason,actor?,changes?:{health,stress,armor,dead,zone,hidden},clear_pending?:true}.

    This is an explicit override, not a claim that an unsupported rule was
    automated. Profile/content versions cannot be patched through this tool.
    """
    return _json_command(session_id, ruling, "override", request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_creature_import(
    session_id: int, actor_id: str, template_id: str, name: str, request_id: str, expected_revision: int
) -> str:
    """GM-only: create a creature actor from a fully validated local signature table."""
    return _mutate(
        session_id,
        {"op": "creature_import", "actor": actor_id, "template": template_id, "name": name},
        request_id,
        expected_revision,
    )


@mcp.tool()
@alien_only
def alien_hazard(session_id: int, hazard: str, request_id: str, expected_revision: int, seed: int | None = None) -> str:
    """GM-only: apply {actor,kind:air|hunger|sleep|cold|heat|hypersleep|vacuum|disease,unit?,penalty?}. Disease starts with a passive exposure check."""
    return _json_command(session_id, hazard, "hazard", request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_environment(
    session_id: int, event: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: resolve {action:blast|fire_set|fall|extinguish|radiation|leave_radiation|breach|shoot_hull|countdown_set,...}. Radiation resolves one exposure and schedules later exposures."""
    return _json_command(session_id, event, "environment", request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_configure(session_id: int, options: str, request_id: str, expected_revision: int) -> str:
    """GM-only: set {mental_trauma,aliens_mode,fixed_initiative}. Trauma requires opt-in plus a complete local D66 table."""
    try:
        return _mutate(session_id, {"op": "configure", "options": _object(options)}, request_id, expected_revision)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_trauma(session_id: int, intent: str, request_id: str, expected_revision: int, seed: int | None = None) -> str:
    """GM-only: op trauma {actor,chapter}, trauma_decide {actor,agenda?|personality?} or trauma_event {actor,event,reason}."""
    try:
        parsed = _object(intent)
        if parsed.get("op") not in {"trauma", "trauma_decide", "trauma_event", "trauma_due_decide"}:
            raise LoreKitError("Unsupported trauma operation")
        return _mutate(session_id, parsed, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_talent(session_id: int, intent: str, request_id: str, expected_revision: int, seed: int | None = None) -> str:
    """GM-only: op talent_use {actor,talent,...} or talent_answer {actor,question,answer}.

    Supports explicit company_resources, nurse, analysis, xenomorphology, pull_rank,
    and counselor uses. Completed examination/care time must be confirmed; elapsed
    clocks are advanced separately. Analysis and weaknesses require recorded GM answers.
    """
    try:
        parsed = _object(intent)
        if parsed.get("op") not in {"talent_use", "talent_answer"}:
            raise LoreKitError("Unsupported talent operation")
        return _mutate(session_id, parsed, request_id, expected_revision, seed)
    except LoreKitError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
@alien_only
def alien_horror(session_id: int, event: str, request_id: str, expected_revision: int, seed: int | None = None) -> str:
    """GM-only: {actor,trigger:first_sight|adjacent|synthetic_reveal|crew_attack|horrifying_event,source?,terrifying?,same_crew?,reason?}.

    First sight is remembered per actor/source. Adjacent terror and arbitrary
    horror require explicit GM context; synthetic revelation changes public facts.
    """
    return _json_command(session_id, event, "horror", request_id, expected_revision, seed)


@mcp.tool()
@alien_only
def alien_check_benefits(session_id: int, choice: str, request_id: str, expected_revision: int) -> str:
    """GM-only: {actor,check_id,benefits:[descriptions]} spends extra successes from a completed generic check.

    Combat, recovery and talent checks spend successes through their own resolver.
    A new generic check replaces this actor's unspent narrative-success balance.
    """
    return _json_command(session_id, choice, "check_benefits", request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_hazard_remedy(session_id: int, remedy: str, request_id: str, expected_revision: int) -> str:
    """GM-only: {actor,hazard,reason,meal?} records removal of a fatigue/vacuum source; this does not heal lost Health."""
    return _json_command(session_id, remedy, "hazard_remedy", request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_damage(
    session_id: int,
    actor_id: str,
    amount: int,
    reason: str,
    request_id: str,
    expected_revision: int,
    seed: int | None = None,
) -> str:
    """GM-only: apply explicitly adjudicated normal damage through armor, criticals and reactions.

    Use the attack/environment operations for their specific modifiers and hazards.
    """
    if not isinstance(reason, str) or not reason.strip():
        return "ERROR: Damage requires a concrete GM reason"
    return _mutate(
        session_id,
        {"op": "damage", "actor": actor_id, "amount": amount, "reason": reason},
        request_id,
        expected_revision,
        seed,
    )


@mcp.tool()
@alien_only
def alien_disclose(session_id: int, disclosure: str, request_id: str, expected_revision: int) -> str:
    """GM-only: {actor,fields:[health|health_max|stress|story_points|species],reason} chooses public mechanical facts.

    Default public actors have the same shape regardless of secret identity.
    Secret synthetic species must first use the revelation horror trigger.
    """
    return _json_command(session_id, disclosure, "disclose", request_id, expected_revision)


@mcp.tool()
@alien_only
def alien_lifecycle(
    session_id: int, pacing: str, request_id: str, expected_revision: int, seed: int | None = None
) -> str:
    """GM-only: {actor,kind:deadline|resolve|stasis,reason,shift?|active?} records source-authorized gestation pacing.

    Stasis pauses infection/incubation only; general ship travel and stasis medicine
    are deferred. Resolve can explicitly advance a life-cycle event before its
    default deadline, with spawned identities and witness effects in the same audit.
    """
    return _json_command(session_id, pacing, "lifecycle", request_id, expected_revision, seed)
