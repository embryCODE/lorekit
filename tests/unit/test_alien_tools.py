"""MCP boundary checks for Alien command isolation and generic-writer bypasses."""

import json

from lorekit.alien.state import load
from lorekit.db import require_db
from lorekit.tools.alien import alien_character_import, alien_check, alien_check_decide, alien_scene_status
from lorekit.tools.character import character_set_attr, character_view
from lorekit.tools.session import session_meta_set


def test_named_tools_preserve_decision_and_block_generic_writer(make_session):
    sid = make_session(system="alien-evolved")
    build = json.dumps({"name": "Engineer", "attributes": dict(strength=3, agility=3, wits=4, empathy=4)})
    created = json.loads(alien_character_import(sid, "engineer", build, "import", 0))
    cid = created["character_id"]
    assert character_set_attr(cid, "stat", "health", "900").startswith("ERROR")
    assert session_meta_set(sid, "alien_evolved_state", "{}").startswith("ERROR")
    assert session_meta_set(sid, "rules_system", "basic").startswith("ERROR")
    assert "Engineer" in character_view(cid)
    check = json.loads(alien_check(sid, json.dumps({"actor": "engineer", "skill": "comtech"}), "check", 1, 1))
    assert check["visibility"] == "gm_only"
    p = check["pending"]
    decided = json.loads(
        alien_check_decide(
            sid, json.dumps({"actor": "engineer", "decision_id": p["id"], "choice": "accept"}), "accept", 2
        )
    )
    assert decided["type"] == "check_complete"
    assert json.loads(alien_scene_status(sid))["revision"] == 3
    assert "audit" not in json.loads(alien_scene_status(sid))
    wrong = make_session(system="dnd55e")
    assert alien_scene_status(wrong).startswith("ERROR")


def test_override_is_explicit_atomic_and_cannot_break_species_invariants(make_session):
    from lorekit.tools.alien import alien_rules_override

    sid = make_session(system="alien-evolved")
    build = json.dumps(
        {"name": "Synthetic", "species": "synthetic", "attributes": dict(strength=3, agility=3, wits=4, empathy=4)}
    )
    alien_character_import(sid, "synthetic", build, "import", 0)
    failed = alien_rules_override(
        sid, json.dumps({"actor": "synthetic", "reason": "invalid trial", "changes": {"stress": 2}}), "invalid", 1
    )
    assert failed.startswith("ERROR")
    assert json.loads(alien_scene_status(sid))["revision"] == 1
    changed = json.loads(
        alien_rules_override(
            sid,
            json.dumps({"actor": "synthetic", "reason": "GM adjudicates a malfunction", "changes": {"health": 1}}),
            "ruling",
            1,
        )
    )
    assert changed["type"] == "gm_override"
    assert changed["reason"] == "GM adjudicates a malfunction"
    assert changed["revision"] == 2


def test_batch_rejection_precedes_narrative_changes_and_exports_are_labeled(make_session, tmp_path, monkeypatch):
    from lorekit.support import export
    from lorekit.tools.character import character_sheet_update

    sid = make_session(system="alien-evolved")
    build = json.dumps(
        {
            "name": "Crew",
            "attributes": dict(strength=3, agility=3, wits=4, empathy=4),
            "private": {"agenda": "Only the GM knows"},
        }
    )
    cid = json.loads(alien_character_import(sid, "crew", build, "import", 0))["character_id"]
    before_db = require_db()
    before = before_db.execute("SELECT gender,prefetch FROM characters WHERE id=?", (cid,)).fetchone()
    result = character_sheet_update(
        cid, gender="changed", prefetch=0, attrs='[{"category":"stat","key":"health","value":"99"}]'
    )
    assert result.startswith("ERROR")
    assert before_db.execute("SELECT gender,prefetch FROM characters WHERE id=?", (cid,)).fetchone() == before
    monkeypatch.setattr(export, "_get_export_dir", lambda: str(tmp_path / "exports"))
    result = export.dump(before_db, sid)
    text = (tmp_path / "exports" / f"session_{sid}.txt").read_text()
    assert "GM-ONLY EXPORT" in text
    assert "Only the GM knows" in text
    public = json.loads(alien_scene_status(sid))
    assert "Only the GM knows" not in json.dumps(public)
    before_db.close()


def test_legacy_mechanical_readers_redirect_and_public_actor_lookup_fails_closed(make_session):
    from lorekit.tools.alien import alien_character_view
    from lorekit.tools.character import character_get_abilities, character_get_attr, character_get_items

    sid = make_session(system="alien-evolved")
    build = json.dumps({"name": "Crew", "attributes": dict(strength=3, agility=3, wits=4, empathy=4)})
    cid = json.loads(alien_character_import(sid, "crew", build, "import", 0))["character_id"]
    for read in (character_get_attr, character_get_items, character_get_abilities):
        assert "Use alien_character_view" in read(cid)
    assert json.loads(alien_character_view(sid, "crew"))["actor"]["name"] == "Crew"
    missing = alien_character_view(sid, "missing")
    foreign = make_session(system="alien-evolved")
    assert missing == alien_character_view(foreign, "crew") == "ERROR: Actor not found or not public"
    assert json.loads(alien_scene_status(sid))["revision"] == 1
