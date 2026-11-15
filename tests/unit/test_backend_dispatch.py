"""Rules-backend dispatch: the session's system picks cruncher tools or backend tools."""

from lorekit import backends
from lorekit.backends import Backend, backend_only, cruncher_only
from lorekit.tools.character import character_create
from lorekit.tools.encounter import encounter_start, encounter_status
from lorekit.tools.rules import rules_check


def test_cruncher_tool_refused_on_backend_session(make_session):
    sid = make_session(system="dnd55e")
    out = encounter_start(session_id=sid)
    assert out.startswith("ERROR") and "dnd_" in out
    assert "no encounter" in encounter_status(session_id=sid).lower() or "ERROR" in encounter_status(session_id=sid)


def test_cruncher_tool_by_character_refused_on_backend_session(make_session):
    sid = make_session(system="dnd55e")
    cid = int(character_create(session=sid, name="Valeros", level=1).split(": ")[1])
    out = rules_check(character_id=cid, check="stealth", dc=10)
    assert out.startswith("ERROR") and "dnd_" in out


def test_cruncher_session_unaffected(make_session):
    sid = make_session(system="basic")
    assert "dnd_" not in encounter_start(session_id=sid)


def test_backend_tool_refused_on_cruncher_session(make_session):
    @backend_only
    def dnd_probe(session_id: int) -> str:
        return "ran"

    assert dnd_probe(session_id=make_session(system="pf2e")).startswith("ERROR")
    assert dnd_probe(session_id=make_session(system="dnd55e")) == "ran"


def test_stub_backend_registers_and_dispatches(make_session):
    backends.register("stubsys", Backend(name="stub", tool_prefix="stub_"))
    try:
        sid = make_session(system="stubsys")

        @cruncher_only
        def crunch(session_id: int) -> str:
            return "ran"

        assert "stub_" in crunch(session_id=sid)
        # Nat20 and cruncher paths unaffected
        assert crunch(session_id=make_session(system="basic")) == "ran"
        assert "dnd_" in crunch(session_id=make_session(system="dnd55e"))
    finally:
        backends.BACKENDS.pop("stubsys", None)


def test_unknown_session_does_not_block(make_session):
    @cruncher_only
    def crunch(session_id: int) -> str:
        return "ran"

    assert crunch(session_id=999999) == "ran"


def test_concrete_backend_tools_reject_each_other_and_missing_ids(make_session):
    from lorekit.backends import system_only
    from lorekit.db import require_db

    alien = make_session(system="alien-evolved")
    dnd = make_session(system="dnd55e")

    @backend_only
    def dnd_probe(session_id: int, character_id: int = 0):
        return "ran"

    @system_only("alien-evolved")
    def alien_probe(session_id: int, character_id: int = 0):
        return "ran"

    assert "alien_" in dnd_probe(alien)
    assert "dnd_" in alien_probe(dnd)
    assert alien_probe(alien) == "ran"
    assert dnd_probe(999999).startswith("ERROR")
    assert alien_probe(alien, 999999).startswith("ERROR")
    cid = int(character_create(session=dnd, name="DND actor", level=1).split(": ")[1])
    assert alien_probe(alien, cid).startswith("ERROR")
