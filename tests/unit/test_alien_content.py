"""Local-only original content fixtures and unsupported import boundaries."""

import copy

import pytest

from lorekit.alien import content
from lorekit.alien.characters import build
from lorekit.alien.state import execute, load, verify
from lorekit.db import LoreKitError, require_db


def bundle():
    return {
        "profile": "evolved-beta2",
        "source": "Original test examples; no book catalog",
        "collections": {
            "talents": {"calibrated": {"page": 1, "effects": [{"hook": "resolve_bonus", "value": 1}]}},
            "careers": {
                "test-technician": {
                    "page": 1,
                    "attribute": "wits",
                    "skills": ["comtech", "observation", "survival"],
                    "talents": ["calibrated"],
                }
            },
            "items": {"test-meter": {"page": 1, "weight": 0.5, "capacity": 3, "supply_type": "power", "effects": []}},
        },
    }


def test_content_hash_tamper_missing_and_atomic_state(make_session):
    sid = make_session(system="alien-evolved")
    fingerprint = content.install(bundle())
    assert content.read(fingerprint) == bundle()
    db = require_db()
    execute(db, sid, {"op": "content_import", "hash": fingerprint}, "content", 0, 0)
    verify(load(db, sid))
    path = content.directory() / f"{fingerprint}.json"
    path.write_text("{}")
    with pytest.raises(LoreKitError, match="hash mismatch"):
        execute(db, sid, {"op": "configure", "options": {}}, "config", 1, 1)
    path.unlink()
    with pytest.raises(LoreKitError, match="Missing"):
        content.read(fingerprint)
    db.close()


def test_synthetic_bonuses_and_standard_budget():
    data = {
        "name": "Test",
        "species": "synthetic",
        "career": "test-technician",
        "attributes": {"strength": 2, "agility": 3, "wits": 5, "empathy": 4},
        "skills": {"comtech": 3, "observation": 3, "survival": 3, "mobility": 1},
        "talents": ["calibrated"],
        "synthetic_bonuses": ["wits", "strength"],
    }
    a = build(data, bundle())
    assert a["attributes"]["wits"] == 8
    assert a["attributes"]["strength"] == 5
    assert a["health_max"] == 4
    assert a["resolve"] == 7
    with pytest.raises(LoreKitError, match="14-point"):
        build({**data, "attributes": {**data["attributes"], "agility": 4}}, bundle())


def test_unknown_hook_rejected_before_install():
    value = bundle()
    value["collections"]["items"]["test-meter"]["effects"] = [{"hook": "arbitrary-code"}]
    with pytest.raises(LoreKitError, match="Unsupported"):
        content.install(value)
