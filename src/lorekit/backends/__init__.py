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


def _check(bound: dict, *, want_backend: bool, tool: str) -> str | None:
    from lorekit.db import require_db

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


def _guard(want_backend: bool):
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = dict(sig.bind_partial(*args, **kwargs).arguments)
            err = _check(bound, want_backend=want_backend, tool=fn.__name__)
            return err if err else fn(*args, **kwargs)

        return wrapper

    return deco


cruncher_only = _guard(False)
backend_only = _guard(True)
