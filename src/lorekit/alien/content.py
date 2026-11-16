"""Immutable local rules bundles. No proprietary catalog is shipped with LoreKit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from lorekit.alien.state import PROFILE, canonical, integer
from lorekit.db import LoreKitError

COLLECTIONS = {"careers", "talents", "items", "creatures", "injuries", "traumas"}
# Resolver hook support is deliberately closed until a feature is implemented.
HOOKS = {
    "health_bonus",
    "resolve_bonus",
    "push_attribute",
    "skill_modifier",
    "supply_reduction",
    "carry_multiplier",
    "relieve_stress",
    "stress_gain",
    "injury_modifier",
    "movement_full",
    "immobile",
    "bleeding_on_skill",
    "infection",
    "trauma",
    "attribute_override",
    "substitute_skill",
    "omit_stress",
    "stress_before_roll",
    "stealth_penalty",
    "rest_bonus",
    "zero_g_immunity",
    "resilient",
    "death_modifier",
    "self_rally",
    "machinegunner",
    "field_commander",
    "martial_arts",
    "rapid_fire",
    "haymaker",
    "weapon_modifier",
    "company_resources",
    "analysis",
    "xenomorphology",
    "pull_rank",
    "counselor",
    "nurse",
    "combat_veteran",
    "fast_reflexes",
    "bodyguard",
    "personal_safety",
    "lucky",
    "overkill",
    "quick_shot",
}


def directory() -> Path:
    from lorekit.db import resolve_db_path

    return Path(os.environ.get("LOREKIT_ALIEN_CONTENT_DIR", str(Path(resolve_db_path()[0]).parent / "alien-content")))


def validate(bundle: dict) -> dict:
    if not isinstance(bundle, dict) or set(bundle) != {"profile", "source", "collections"}:
        raise LoreKitError("Bundle requires exactly profile, source and collections")
    if bundle["profile"] != PROFILE or not isinstance(bundle["source"], str) or not bundle["source"].strip():
        raise LoreKitError("Bundle requires evolved-beta2 profile and source provenance")
    collections = bundle["collections"]
    if not isinstance(collections, dict) or not set(collections) <= COLLECTIONS:
        raise LoreKitError("Invalid bundle collections")
    for family, records in collections.items():
        if not isinstance(records, dict):
            raise LoreKitError("Each collection must be an object keyed by stable ID")
        for key, entry in records.items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", key) or not isinstance(entry, dict):
                raise LoreKitError("Invalid content ID or entry")
            if type(entry.get("page")) is not int or entry["page"] < 1:
                raise LoreKitError("Every entry requires a printed source page")
            effects = entry.get("effects", [])
            if not isinstance(effects, list):
                raise LoreKitError("effects must be a list")
            for effect in effects:
                from lorekit.alien.trauma import HOOKS as TRAUMA_HOOKS

                if not isinstance(effect, dict) or effect.get("hook") not in (
                    TRAUMA_HOOKS if family == "traumas" else HOOKS
                ):
                    raise LoreKitError("Unsupported content effect hook")
                validate_effect(effect)
                hook = effect["hook"]
                if hook in ("health_bonus", "resolve_bonus", "carry_multiplier", "supply_reduction"):
                    integer(effect.get("value"), "effect value", 0, 10)
                if hook == "push_attribute" and effect.get("attribute") not in (
                    "strength",
                    "agility",
                    "wits",
                    "empathy",
                ):
                    raise LoreKitError("Invalid push attribute")
                if hook in ("attribute_override", "substitute_skill"):
                    from lorekit.alien.characters import ATTRIBUTES, SKILLS

                    if effect.get("skill") not in SKILLS:
                        raise LoreKitError("Invalid talent skill")
                    if hook == "attribute_override" and effect.get("attribute") not in ATTRIBUTES:
                        raise LoreKitError("Invalid talent attribute")
                    if hook == "substitute_skill" and effect.get("to") not in SKILLS:
                        raise LoreKitError("Invalid substituted skill")
                if hook in ("rest_bonus", "stealth_penalty"):
                    integer(effect.get("value"), "talent bonus", 1, 2)
                if hook in ("omit_stress", "stress_before_roll"):
                    integer(effect.get("value"), "stress hook value", 1, 2)
                if hook == "skill_modifier":
                    if type(effect.get("value")) is not int or not isinstance(effect.get("context"), str):
                        raise LoreKitError("skill_modifier needs integer value and context")
            for talent in entry.get("talents", []):
                if talent not in collections.get("talents", {}):
                    raise LoreKitError("Missing referenced talent")
            if family in ("careers", "injuries", "traumas"):
                allowed = {
                    "careers": {"page", "attribute", "skills", "talents"},
                    "injuries": {
                        "page",
                        "effects",
                        "interval",
                        "healing",
                        "lethal",
                        "instant_death",
                        "surgery",
                        "prosthesis",
                    },
                    "traumas": {"page", "effects"},
                }[family]
                if set(entry) - allowed:
                    raise LoreKitError("Unsupported content entry property")
            if family == "traumas":
                from lorekit.alien.trauma import HOOKS as TRAUMA_HOOKS
                from lorekit.alien.trauma import TRAITS

                if key not in {str(t * 10 + u) for t in range(1, 7) for u in range(1, 7)}:
                    raise LoreKitError("Trauma ID must be a D66 result")
                for effect in effects:
                    if effect["hook"] not in TRAUMA_HOOKS:
                        raise LoreKitError("Unsupported trauma effect")
                    if effect["hook"] == "trauma_trait" and effect.get("trait") not in TRAITS:
                        raise LoreKitError("Unsupported trauma trait")
                    if (
                        effect["hook"] in ("resolve_delta", "attribute_penalty", "trauma_threshold")
                        and type(effect.get("value")) is not int
                    ):
                        raise LoreKitError("Trauma numeric effect requires an integer")
                    if effect["hook"] == "attribute_penalty" and effect.get("attribute") not in (
                        "strength",
                        "agility",
                        "wits",
                        "empathy",
                    ):
                        raise LoreKitError("Invalid trauma attribute")
            if family == "talents" and any(
                e["hook"]
                in {
                    "relieve_stress",
                    "stress_gain",
                    "injury_modifier",
                    "movement_full",
                    "immobile",
                    "bleeding_on_skill",
                    "infection",
                    "trauma",
                }
                for e in effects
            ):
                raise LoreKitError("Injury hooks cannot be used as talent effects")
            if family == "talents":
                if set(entry) - {"page", "effects", "max_selections"}:
                    raise LoreKitError("Unsupported talent property")
                integer(entry.get("max_selections", 1), "talent selection limit", 1, 3)
                for effect in effects:
                    if effect["hook"] == "death_modifier":
                        integer(effect.get("value"), "death modifier", 1, 2)
                    if effect["hook"] == "weapon_modifier":
                        if effect.get("weapon") != "unarmed" and effect.get("weapon") not in collections.get(
                            "items", {}
                        ):
                            raise LoreKitError("Missing specialized weapon model")
                        integer(effect.get("value"), "specialization modifier", 1, 2)
            if family == "talents" and not effects:
                raise LoreKitError("Unsupported talent without implemented effect hooks")
            if family == "injuries":
                if any(
                    e["hook"]
                    not in {
                        "relieve_stress",
                        "stress_gain",
                        "injury_modifier",
                        "movement_full",
                        "immobile",
                        "bleeding_on_skill",
                        "infection",
                        "trauma",
                    }
                    for e in effects
                ):
                    raise LoreKitError("Unsupported injury effect; additional resolver required")
                for effect in effects:
                    if effect["hook"] in ("stress_gain", "injury_modifier") and type(effect.get("value")) is not int:
                        raise LoreKitError("Injury modifier/stress effect requires an integer value")
                if key not in {str(t * 10 + u) for t in range(1, 7) for u in range(1, 7)}:
                    raise LoreKitError("Injury ID must be a D66 result")
                if entry.get("interval") not in (None, "round", "stretch", "shift"):
                    raise LoreKitError("Invalid lethal injury interval")
                healing = entry.get("healing")
                if healing is not None:
                    if (
                        not isinstance(healing, dict)
                        or set(healing) != {"amount", "unit"}
                        or healing["unit"] not in ("day", "shift")
                    ):
                        raise LoreKitError("Invalid injury healing duration")
                    amount = healing["amount"]
                    if not (
                        type(amount) is int
                        and amount > 0
                        or isinstance(amount, str)
                        and re.fullmatch(r"[1-4]d6", amount)
                    ):
                        raise LoreKitError("Invalid injury healing dice")
                for flag in ("lethal", "instant_death", "surgery", "prosthesis"):
                    if flag in entry and type(entry[flag]) is not bool:
                        raise LoreKitError("Injury flags must be boolean")
            if family == "items":
                known = {
                    "page",
                    "weight",
                    "capacity",
                    "supply_type",
                    "effects",
                    "damage",
                    "modifier",
                    "minimum",
                    "maximum",
                    "piercing",
                    "full_auto",
                    "aim_required",
                    "unreliable",
                    "single_shot",
                    "tracker_range",
                    "stun",
                    "armor",
                    "acid_proof",
                    "facehug_proof",
                    "vacuum_proof",
                    "check_modifiers",
                    "single_use",
                    "category",
                    "explosive",
                }
                if set(entry) - known:
                    raise LoreKitError("Unsupported item property")
                if effects:
                    raise LoreKitError("Item effect hooks are not installed; use supported item fields")
                modifiers = entry.get("check_modifiers", [])
                if not isinstance(modifiers, list):
                    raise LoreKitError("Item check_modifiers must be a list")
                from lorekit.alien.characters import SKILLS

                for modifier in modifiers:
                    if (
                        not isinstance(modifier, dict)
                        or modifier.get("skill") not in SKILLS
                        or type(modifier.get("value")) is not int
                        or not isinstance(modifier.get("context"), str)
                    ):
                        raise LoreKitError("Invalid contextual item modifier")
                if "category" in entry and entry["category"] not in ("pistol", "rifle", "heavy", "melee"):
                    raise LoreKitError("Unknown weapon category")
                if "damage" in entry:
                    integer(entry["damage"], "weapon damage", 1)
                    integer(entry.get("minimum", 0), "minimum range", 0, 4)
                    integer(entry.get("maximum", 0), "maximum range", entry.get("minimum", 0), 4)
                    if type(entry.get("modifier", 0)) is not int:
                        raise LoreKitError("weapon modifier must be integer")
                for flag in ("piercing", "full_auto", "aim_required", "unreliable", "single_shot", "stun", "explosive"):
                    if flag in entry and type(entry[flag]) is not bool:
                        raise LoreKitError("weapon flags must be boolean")
                if "armor" in entry:
                    integer(entry["armor"], "armor level")
                if "tracker_range" in entry:
                    integer(entry["tracker_range"], "tracker range", 1)
                for flag in ("acid_proof", "facehug_proof", "vacuum_proof"):
                    if flag in entry and type(entry[flag]) is not bool:
                        raise LoreKitError("Item protection flags must be boolean")
                weight = entry.get("weight", 1)
                if type(weight) not in (int, float) or weight < 0:
                    raise LoreKitError("Item weight must be nonnegative")
                integer(entry.get("capacity", 0), "capacity")
                if entry.get("supply_type") not in (None, "air", "ammo", "power"):
                    raise LoreKitError("Unsupported supply type")
            if family == "careers":
                from lorekit.alien.characters import ATTRIBUTES, SKILLS

                if entry.get("attribute") not in ATTRIBUTES or not isinstance(entry.get("skills"), list):
                    raise LoreKitError("Career requires key attribute and skill list")
                if len(entry["skills"]) != 3 or not set(entry["skills"]) <= set(SKILLS):
                    raise LoreKitError("Career requires three valid key skills")
            if family == "creatures":
                allowed = {
                    "page",
                    "effects",
                    "health",
                    "speed",
                    "skills",
                    "armor",
                    "acid",
                    "early_stage",
                    "stage",
                    "spawn",
                    "birth",
                    "matures",
                    "armor_fire",
                    "stealth_penalty",
                    "attacks",
                }
                if set(entry) - allowed:
                    raise LoreKitError("Unsupported creature property")
                for number in ("acid", "armor_fire", "stealth_penalty"):
                    if number in entry:
                        integer(entry[number], f"creature {number}")
                if "early_stage" in entry and type(entry["early_stage"]) is not bool:
                    raise LoreKitError("early_stage must be boolean")
                from lorekit.alien.characters import SKILLS
                from lorekit.alien.creatures import ATTACK_HOOKS

                for reference in ("birth", "matures"):
                    if entry.get(reference) is not None and entry[reference] not in collections.get("creatures", {}):
                        raise LoreKitError("Missing referenced creature life-cycle template")
                integer(entry.get("health"), "creature Health", 1)
                integer(entry.get("speed"), "creature Speed", 0 if entry.get("stage") == "egg" else 1, 10)
                integer(entry.get("armor", 0), "creature armor")
                if not isinstance(entry.get("skills"), dict) or not set(entry["skills"]) <= set(SKILLS):
                    raise LoreKitError("Creature requires skill-only ratings")
                for value in entry["skills"].values():
                    integer(value, "creature skill")
                if entry.get("stage") == "egg":
                    if entry.get("spawn") not in collections.get("creatures", {}) or entry.get("speed") != 0:
                        raise LoreKitError("Egg requires a local hatchling template and Speed zero")
                    continue
                attacks = entry.get("attacks")
                if not isinstance(attacks, dict) or set(attacks) != {str(i) for i in range(1, 7)}:
                    raise LoreKitError("Creature requires all six signature results")
                for attack in attacks.values():
                    if not isinstance(attack, dict) or not isinstance(attack.get("id"), str) or not attack.get("id"):
                        raise LoreKitError("Each signature needs a stable attack ID")
                    if not isinstance(attack.get("actions"), list) or not attack["actions"]:
                        raise LoreKitError("Each signature needs implemented actions")
                    if any(not isinstance(e, dict) or e.get("hook") not in ATTACK_HOOKS for e in attack["actions"]):
                        raise LoreKitError("Unsupported signature attack hook")
    for entry in collections.get("creatures", {}).values():
        for attack in entry.get("attacks", {}).values():
            validate_actions(attack["actions"], collections)
    canonical(bundle)  # Reject NaN/non-JSON values.
    return bundle


def install(bundle: dict) -> str:
    data = canonical(validate(bundle)).encode()
    fingerprint = hashlib.sha256(data).hexdigest()
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".import-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, root / f"{fingerprint}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return fingerprint


def read(fingerprint: str | None) -> dict:
    if fingerprint is None:
        return {"profile": PROFILE, "source": "empty", "collections": {}}
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise LoreKitError("Invalid content fingerprint")
    try:
        data = (directory() / f"{fingerprint}.json").read_bytes()
    except FileNotFoundError as exc:
        raise LoreKitError("Missing Alien content bundle; restore the matching local hash") from exc
    if hashlib.sha256(data).hexdigest() != fingerprint:
        raise LoreKitError("Alien content hash mismatch")
    return validate(json.loads(data))


def validate_actions(actions, collections, depth=0):
    from lorekit.alien.creatures import ATTACK_HOOKS

    if depth > 8 or not isinstance(actions, list) or not actions:
        raise LoreKitError("Invalid or overly nested signature actions")
    fields = {
        "attack": {"dice", "damage", "piercing", "range", "fixed_damage"},
        "stress": {"value"},
        "panic": {"scope"},
        "drop_item": set(),
        "move": {"scope", "zones"},
        "grapple": set(),
        "facehug": set(),
        "instant_kill": set(),
        "next_attack": {"bonus", "force", "requires_grapple"},
        "branch": {"branches"},
        "resist_damage": {"skill", "amount"},
        "critical_injury": {"injury"},
        "knockdown": set(),
        "immobilize": set(),
        "drag": {"zones"},
        "escape": {"zones"},
    }
    for action in actions:
        if not isinstance(action, dict) or action.get("hook") not in ATTACK_HOOKS:
            raise LoreKitError("Unsupported signature hook")
        hook = action["hook"]
        if set(action) - ({"hook", "when"} | fields[hook]) or action.get("when", "always") not in (
            "always",
            "hit",
            "damage",
        ):
            raise LoreKitError("Unsupported signature action property/condition")
        if hook in ("move", "drag", "escape"):
            integer(action.get("zones", 1), "signature movement zones", 1, 2)
            if action.get("scope", "self") not in ("self", "target"):
                raise LoreKitError("Invalid signature movement scope")
        if hook == "panic" and action.get("scope", "target") not in ("target", "friends"):
            raise LoreKitError("Invalid signature panic scope")
        for flag in ("piercing", "fixed_damage", "requires_grapple"):
            if flag in action and type(action[flag]) is not bool:
                raise LoreKitError("Signature flags must be boolean")
        if hook == "resist_damage":
            from lorekit.alien.characters import SKILLS

            if action.get("skill", "stamina") not in SKILLS:
                raise LoreKitError("Invalid signature resistance skill")
        if hook == "attack":
            integer(action.get("dice"), "signature dice", 1)
            integer(action.get("damage", 1), "signature damage")
            integer(action.get("range", 0), "signature range", 0, 4)
        if hook in ("stress", "resist_damage"):
            integer(action.get("value", 1) if hook == "stress" else action.get("amount"), "signature amount")
        if hook == "branch":
            branches = action.get("branches")
            if not isinstance(branches, dict) or set(branches) != {str(i) for i in range(1, 7)}:
                raise LoreKitError("Signature branch requires six outcomes")
            for branch in branches.values():
                validate_actions(branch, collections, depth + 1)
        if hook == "critical_injury" and str(action.get("injury")) not in collections.get("injuries", {}):
            raise LoreKitError("Signature injury reference is missing")
        if hook == "next_attack":
            integer(action.get("bonus", 0), "signature roll bonus", 0, 2)
            if action.get("force") is not None:
                integer(action["force"], "forced signature result", 1, 6)


def validate_effect(effect):
    from lorekit.alien.characters import ATTRIBUTES, SKILLS

    fields = {
        "health_bonus": {"value"},
        "resolve_bonus": {"value"},
        "push_attribute": {"attribute"},
        "skill_modifier": {"value", "skills", "context"},
        "supply_reduction": {"value", "resource"},
        "carry_multiplier": {"value"},
        "stress_gain": {"value"},
        "injury_modifier": {"value", "skills", "condition"},
        "bleeding_on_skill": {"value", "skills"},
        "infection": {"value"},
        "attribute_override": {"skill", "attribute", "context"},
        "substitute_skill": {"skill", "to", "value", "context"},
        "omit_stress": {"value"},
        "stress_before_roll": {"value"},
        "stealth_penalty": {"value"},
        "rest_bonus": {"value"},
        "death_modifier": {"value"},
        "weapon_modifier": {"weapon", "value"},
        "trauma_trait": {"trait"},
        "attribute_penalty": {"attribute", "value"},
        "resolve_delta": {"value"},
        "trauma_threshold": {"value"},
    }
    if set(effect) - ({"hook"} | fields.get(effect["hook"], set())):
        raise LoreKitError("Unsupported effect property")
    if "skills" in effect and (
        not isinstance(effect["skills"], list) or not effect["skills"] or any(s not in SKILLS for s in effect["skills"])
    ):
        raise LoreKitError("Effect requires supported skills")
    if "context" in effect and (not isinstance(effect["context"], str) or not effect["context"]):
        raise LoreKitError("Effect context must be nonempty")
    if "condition" in effect and effect["condition"] not in ("two_hands", "two_arms"):
        raise LoreKitError("Unsupported injury condition")
    if effect["hook"] == "supply_reduction" and effect.get("resource") not in ("ammo", "air", "power"):
        raise LoreKitError("Unsupported reduced supply")
    if effect["hook"] in ("bleeding_on_skill", "infection", "substitute_skill"):
        integer(effect.get("value", 0), "effect value")
    if "attribute" in effect and effect["attribute"] not in ATTRIBUTES:
        raise LoreKitError("Invalid effect attribute")
