"""Closed command envelopes: unsupported mechanics must never be ignored."""

from lorekit.db import LoreKitError

FIELDS = {
    "character_create": "actor build character_id",
    "character_import": "actor build character_id",
    "creature_import": "actor template name character_id",
    "content_import": "hash",
    "configure": "options",
    "override": "reason actor changes clear_pending",
    "disclose": "actor fields reason",
    "scene_setup": "zones borders placements sight crawl_order objects",
    "combat_start": "actors groups surprise ambush",
    "combat_end": "",
    "end_turn": "actor",
    "begin_turn": "actor",
    "hold_off": "actor card",
    "move": "actor zone",
    "approach": "actor target",
    "creature_move": "actor path approach",
    "creature_act": "actor target feasible choices",
    "lifecycle": "actor kind reason active shift",
    "signature_decide": "actor path item",
    "creature_escape_decide": "actor path route_unclear",
    "egg_release": "actor target reason",
    "facehugger_remove": "actor target extreme_cold stretch_completed equipment",
    "attack": "actor target weapon aim weak_spot full_auto conserve known_target special quick_attack haymaker shove_zone order",
    "defense_decide": "actor decision_id choice target friendly weapon unarmed",
    "area_defense": "actor choice",
    "special_decide": "actor item choice zone new_item_id",
    "hit_decide": "actor choice",
    "burst_decide": "actor choice target known_target",
    "initiative_decide": "actor keep card",
    "check": "actor skill attribute modifier helpers remote_help passive open opponent context gear reckless omit_stress talent_substitution order two_hands two_arms nonverbal continuation no_push no_stress",
    "check_decide": "actor decision_id choice die",
    "check_benefits": "actor check_id benefits",
    "stress": "actor reason amount",
    "panic": "actor reason",
    "queue_continue": "",
    "panic_decide": "actor alerted item zone path safe away_from_threat target weapon approach",
    "overkill_decide": "actor choice enemies",
    "injury_decide": "actor choice",
    "horror": "actor source trigger terrifying same_crew reason",
    "story_award": "actor reason amount act_id",
    "supply": "actor item",
    "time_advance": "unit amount",
    "stealth_act": "actor action zone path group description",
    "stealth_phase": "elapsed",
    "leave_combat": "actor route_unclear",
    "scene_mode": "mode",
    "search_decide": "actor found",
    "detection_decide": "actor choice decision_id",
    "tracker": "actor item",
    "damage": "actor amount reason",
    "recover": "actor actors target method safe interrupted remove_response remove_responses alcohol comms shift_completed stretch_completed injury gear power prosthesis two_hands two_arms",
    "hazard": "actor kind unit penalty meal",
    "hazard_remedy": "actor hazard reason meal",
    "environment": "action actor target zone zones intensity level meters controlled power automatic_seal weapon minutes",
    "action": "actor action target zone item active key cover barrier_armor comms order stretch_completed weapon equipped skill cost owner new_item_id object",
    "talent_use": "actor talent act_id content_id item patients shift_completed object stretch_completed target same_organization non_officer order comms",
    "talent_answer": "actor question answer",
    "trauma": "actor chapter",
    "trauma_decide": "actor agenda personality",
    "trauma_event": "actor event reason",
    "trauma_due_decide": "actor reason",
}
BOOLS = set(
    "aim weak_spot full_auto conserve known_target quick_attack haymaker passive open remote_help talent_substitution reckless nonverbal comms safe interrupted alcohol stretch_completed shift_completed same_organization non_officer automatic_seal friendly key active keep route_unclear same_crew terrifying clear_pending unarmed extreme_cold controlled two_hands two_arms elapsed meal equipped prosthesis".split()
)


def validate(intent):
    if not isinstance(intent, dict) or not isinstance(intent.get("op"), str):
        raise LoreKitError("Alien intent requires a supported operation")
    op = intent["op"]
    if op not in FIELDS:
        raise LoreKitError(f"Unsupported Alien operation: {op}")
    if set(intent) - ({"op"} | set(FIELDS[op].split())):
        raise LoreKitError("Unsupported Alien intent property")
    validate_detail(intent)
    for key in BOOLS & set(intent):
        if type(intent[key]) is not bool:
            raise LoreKitError(f"{key} must be boolean")


ACTION_FIELDS = {
    "help": "target skill",
    "take_object": "object item",
    "drop": "item",
    "equip": "item equipped",
    "suit_enter": "item",
    "consume": "target item cost",
    "seal_access": "zone item",
    "block": "zone",
    "unblock": "zone",
    "allow_pass": "zone target",
    "door_lock": "zone",
    "door_unlock": "zone key",
    "door_peek": "zone",
    "door_hack": "zone",
    "door_break": "zone weapon",
    "drag": "target zone",
    "air_active": "item active",
    "reload": "item",
    "clear_jam": "item",
    "cover": "cover barrier_armor",
    "pickup": "item owner new_item_id stretch_completed",
    "release": "",
    "escape": "",
    "orders": "target comms order",
}
ENVIRONMENT_FIELDS = {
    "blast": "zone power",
    "fire_set": "zone intensity",
    "fall": "actor meters controlled",
    "extinguish": "actor target",
    "radiation": "actor level",
    "leave_radiation": "actor",
    "breach": "zones automatic_seal",
    "shoot_hull": "actor weapon",
    "countdown_set": "minutes",
}
TALENT_FIELDS = {
    "company_resources": "act_id content_id item",
    "nurse": "patients shift_completed",
    "analysis": "object stretch_completed",
    "xenomorphology": "target",
    "pull_rank": "target same_organization non_officer order comms",
    "counselor": "target",
}


def validate_detail(intent):
    op = intent["op"]
    if op == "action":
        selector, fields, common = intent.get("action"), ACTION_FIELDS, {"op", "actor", "action"}
    elif op == "environment":
        selector, fields, common = intent.get("action"), ENVIRONMENT_FIELDS, {"op", "action"}
    elif op == "talent_use":
        selector, fields, common = intent.get("talent"), TALENT_FIELDS, {"op", "actor", "talent"}
    else:
        selector = None
    if op in ("action", "environment", "talent_use"):
        if (
            not isinstance(selector, str)
            or selector not in fields
            or set(intent) - (common | set(fields[selector].split()))
        ):
            raise LoreKitError("Unsupported action/feature property")
    if op == "check" and intent.get("opponent") is not None:
        opponent = intent["opponent"]
        allowed = set(FIELDS["check"].split()) - {"continuation", "no_push", "no_stress", "opponent", "open"}
        if not isinstance(opponent, dict) or set(opponent) - allowed:
            raise LoreKitError("Unsupported opponent check property")
