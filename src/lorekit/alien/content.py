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
                if not isinstance(effect, dict) or effect.get("hook") not in HOOKS:
                    raise LoreKitError("Unsupported content effect hook")
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
                if hook in ("omit_stress", "stress_before_roll"):
                    integer(effect.get("value"), "stress hook value", 1, 2)
                if hook == "skill_modifier":
                    if type(effect.get("value")) is not int or not isinstance(effect.get("context"), str):
                        raise LoreKitError("skill_modifier needs integer value and context")
            for talent in entry.get("talents", []):
                if talent not in collections.get("talents", {}):
                    raise LoreKitError("Missing referenced talent")
            if family == "traumas":
                raise LoreKitError("Unsupported trauma resolver; do not import inert trauma entries")
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
            if family == "talents" and not effects:
                raise LoreKitError("Unsupported talent without implemented effect hooks")
            if family == "injuries":
                if any(
                    e["hook"] not in {"relieve_stress", "stress_gain", "injury_modifier", "movement_full", "immobile"}
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
                for flag in ("lethal", "instant_death", "surgery"):
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
                }
                if set(entry) - known:
                    raise LoreKitError("Unsupported item property")
                if effects:
                    raise LoreKitError("Item effect hooks are not installed; use supported item fields")
                if "damage" in entry:
                    integer(entry["damage"], "weapon damage", 1)
                    integer(entry.get("minimum", 0), "minimum range", 0, 4)
                    integer(entry.get("maximum", 0), "maximum range", entry.get("minimum", 0), 4)
                    if type(entry.get("modifier", 0)) is not int:
                        raise LoreKitError("weapon modifier must be integer")
                for flag in ("piercing", "full_auto", "aim_required", "unreliable", "single_shot", "stun"):
                    if flag in entry and type(entry[flag]) is not bool:
                        raise LoreKitError("weapon flags must be boolean")
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
                from lorekit.alien.characters import SKILLS
                from lorekit.alien.creatures import ATTACK_HOOKS

                integer(entry.get("health"), "creature Health", 1)
                integer(entry.get("speed"), "creature Speed", 1, 10)
                integer(entry.get("armor", 0), "creature armor")
                if not isinstance(entry.get("skills"), dict) or not set(entry["skills"]) <= set(SKILLS):
                    raise LoreKitError("Creature requires skill-only ratings")
                for value in entry["skills"].values():
                    integer(value, "creature skill")
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
