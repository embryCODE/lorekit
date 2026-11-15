# lorekit

MCP game engine plus a standalone TTRPG rules engine (`cruncher/`). See README.md and ARCHITECTURE.md.

## Setup and tests

```bash
uv sync --all-extras
uv pip install -e systems/mm3e -e systems/pf2e   # system packs are not covered by uv sync
uv run --no-sync pytest
```

`uv run pytest` (without `--no-sync`) can drop the editable system packs; use `--no-sync`.
Lint: `ruff` (see `.pre-commit-config.yaml`). Python deps are managed with uv.

## Conventions

- Prefer small engine PRs that could go upstream (`matluz1/lorekit`) over forking cruncher heavily.
- Specs and change tracking live in the parent orchestrator repo (`openspec/changes/`), not here.
