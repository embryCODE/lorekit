"""Exercise real MCP stdio calls with an isolated database and no model provider."""

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_alien_tools_over_real_mcp_stdio(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "import lorekit.tools; from lorekit._mcp_app import mcp; mcp.run()"],
        env={**os.environ, "LOREKIT_DB_DIR": str(tmp_path), "LOREKIT_DB": str(tmp_path / "stdio.db")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "alien_check_decide" in {tool.name for tool in tools.tools}

            async def call(tool_name, **args):
                result = await session.call_tool(tool_name, args)
                assert not result.isError, result
                return "\n".join(part.text for part in result.content if hasattr(part, "text"))

            setup = await call(
                "session_setup", name="Isolated Alien test", setting="Original test station", system="alien-evolved"
            )
            sid = int(setup.split("SESSION_CREATED: ")[1].splitlines()[0])
            imported = json.loads(
                await call(
                    "alien_character_import",
                    session_id=sid,
                    actor_id="engineer",
                    build=json.dumps(
                        {"name": "Engineer", "attributes": dict(strength=3, agility=3, wits=4, empathy=4)}
                    ),
                    request_id="import",
                    expected_revision=0,
                )
            )
            assert imported["revision"] == 1
            rolled = json.loads(
                await call(
                    "alien_check",
                    session_id=sid,
                    check=json.dumps({"actor": "engineer", "skill": "comtech"}),
                    request_id="roll",
                    expected_revision=1,
                    seed=1,
                )
            )
            pending = rolled["pending"]
            decided = json.loads(
                await call(
                    "alien_check_decide",
                    session_id=sid,
                    decision=json.dumps({"actor": "engineer", "decision_id": pending["id"], "choice": "accept"}),
                    request_id="accept",
                    expected_revision=2,
                )
            )
            assert decided["type"] == "check_complete"
            public = json.loads(await call("alien_scene_status", session_id=sid))
            assert public["revision"] == 3
            assert "private" not in json.dumps(public)
            wrong = await call("dnd_combat_status", session_id=sid)
            assert wrong.startswith("ERROR") and "alien_" in wrong
