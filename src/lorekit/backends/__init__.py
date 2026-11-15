"""Rules backends: external engines that resolve a system's rules instead of cruncher.

A session's ``system_type`` selects its backend. Systems with no registered
backend use cruncher and the pack-driven tools. Nothing here assumes d20 rolls
or a grid, so a dice-pool backend can register next to Nat20.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    name: str
    tool_prefix: str
    families: frozenset[str] = frozenset({"combat", "check", "rest", "build"})


# system name -> backend. Systems absent from this map run on cruncher.
BACKENDS: dict[str, Backend] = {
    "dnd55e": Backend(name="nat20", tool_prefix="dnd_"),
    "alien-evolved": Backend(name="alien", tool_prefix="alien_"),
}


def register(system: str, backend: Backend) -> None:
    BACKENDS[system] = backend


def backend_for_system(system: str | None) -> Backend | None:
    return BACKENDS.get(system or "")


def session_backend(db, session_id: int) -> Backend | None:
    row = db.execute("SELECT system_type FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return backend_for_system(row[0] if row else None)


def _session_of(bound: dict) -> int | None:
    """Best-effort session lookup from a tool call's arguments."""
    from lorekit.db import require_db
    from lorekit.queries import get_character_session_id
    from lorekit.tools._helpers import _resolve_character

    db = require_db()
    try:
        if isinstance(bound.get("session_id"), int) and bound["session_id"]:
            return bound["session_id"]
        for key in ("character_id", "character"):
            if bound.get(key) not in (None, ""):
                return get_character_session_id(db, _resolve_character(db, bound[key]))
    except Exception:
        return None
    finally:
        db.close()
    return None


def _strict_session(db, bound: dict) -> int:
    """Resolve every supplied reference; never trust numeric passthrough IDs."""
    import json

    from lorekit.db import LoreKitError
    from lorekit.tools._helpers import _resolve_character

    sessions = set()
    for key in ("session_id", "session"):
        value = bound.get(key)
        if value not in (None, "", 0):
            row = db.execute("SELECT id FROM sessions WHERE id = ?", (value,)).fetchone()
            if row is None:
                raise LoreKitError("Session not found")
            sessions.add(row[0])
    identifiers = [bound[k] for k in ("character_id", "character", "target_id", "target") if bound.get(k)]
    for key in ("party", "cells", "intent"):
        raw = bound.get(key)
        if not raw:
            continue
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError) as exc:
            raise LoreKitError(f"Invalid {key} JSON") from exc
        if key == "party" and isinstance(value, list):
            identifiers.extend(value)
        elif key == "cells" and isinstance(value, dict):
            identifiers.extend(value)
        elif key == "intent" and isinstance(value, dict):
            for field in ("target_id", "target_ids"):
                targets = value.get(field, [])
                targets = targets if isinstance(targets, list) else [targets]
                identifiers.extend(t for t in targets if isinstance(t, str) and t.startswith("char:"))
    actor = bound.get("actor")
    if isinstance(actor, str) and actor.startswith("char:"):
        identifiers.append(actor)
    for identifier in identifiers:
        if isinstance(identifier, str) and identifier.startswith("char:"):
            identifier = identifier[5:]
        cid = _resolve_character(db, identifier, next(iter(sessions)) if len(sessions) == 1 else None)
        row = db.execute("SELECT session_id FROM characters WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise LoreKitError("Character not found")
        sessions.add(row[0])
    if len(sessions) != 1:
        raise LoreKitError("Exactly one matching session is required for all references")
    return sessions.pop()


def _check(bound: dict, *, want_backend: bool, tool: str, expected_system: str | None = None) -> str | None:
    from lorekit.db import require_db

    if expected_system is not None:
        from lorekit.db import LoreKitError

        db = require_db()
        try:
            sid = _strict_session(db, bound)
            row = db.execute("SELECT system_type FROM sessions WHERE id = ?", (sid,)).fetchone()
            if row[0] != expected_system:
                be = backend_for_system(row[0])
                family = be.tool_prefix if be else "cruncher "
                return f"ERROR: {tool} requires {expected_system}; use {family}tools for this session."
            return None
        except (LoreKitError, TypeError, ValueError) as exc:
            return f"ERROR: {exc}"
        finally:
            db.close()
    sid = _session_of(bound)
    if sid is None:
        return None
    db = require_db()
    try:
        be = session_backend(db, sid)
    finally:
        db.close()
    if want_backend and be is None:
        return f"ERROR: {tool} is for backend-resolved systems (e.g. dnd55e); session {sid} uses a cruncher pack."
    if not want_backend and be is not None:
        return (
            f"ERROR: {tool} is a cruncher tool; session {sid} is resolved by {be.name}. "
            f"Use the {be.tool_prefix}* tools instead."
        )
    return None


def _guard(want_backend: bool, expected_system: str | None = None):
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = dict(sig.bind_partial(*args, **kwargs).arguments)
            err = _check(bound, want_backend=want_backend, tool=fn.__name__, expected_system=expected_system)
            return err if err else fn(*args, **kwargs)

        return wrapper

    return deco


cruncher_only = _guard(False)
backend_only = _guard(True, "dnd55e")


def system_only(system: str):
    """Guard a concrete backend's tools, including ownership of every reference."""
    return _guard(True, system)


def reject_alien_mechanical_update(db, character_id: int) -> None:
    """Generic row writers cannot bypass Alien's canonical state transaction."""
    from lorekit.db import LoreKitError

    row = db.execute(
        "SELECT s.system_type FROM sessions s JOIN characters c ON c.session_id=s.id WHERE c.id=?", (character_id,)
    ).fetchone()
    if row and row[0] == "alien-evolved":
        raise LoreKitError("Alien mechanical state must be changed through alien_* tools")
