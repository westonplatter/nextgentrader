# Spec: Installable Internal App Layout

## Summary

Migrate project code from flat `src/*.py` modules and `from src...` imports to a proper package layout at `src/ngtrader/...`, while keeping the project installable via uv for internal use.

This is an internal workflow app, not a public distribution target.

## Goals

- Keep an installable app workflow (`uv sync` installs project + deps in editable mode).
- Replace `src` as the import package with `ngtrader`.
- Improve import clarity and long-term maintainability.
- Preserve existing script behavior and operator workflows.

## Non-goals

- Publishing to PyPI.
- Changing runtime behavior of trading/database scripts.
- Redesigning domain model or DB schema.

## Current State

- Modules live directly under `src/` (for example `src/db.py`, `src/models.py`).
- Scripts and Alembic import via `from src...`.
- `pyproject.toml` wheel target currently points at `packages = ["src"]`.

## Decision

Adopt a standard `src` layout with a real package:

```text
src/ngtrader/__init__.py
src/ngtrader/db.py
src/ngtrader/models.py
src/ngtrader/schemas.py
src/ngtrader/utils/...
scripts/...
alembic/...
```

Keep installable-app mode (retain `[build-system]` in `pyproject.toml`).

## Required Changes

1. Move code files:
- `src/db.py` -> `src/ngtrader/db.py`
- `src/models.py` -> `src/ngtrader/models.py`
- `src/schemas.py` -> `src/ngtrader/schemas.py`
- `src/utils/*` -> `src/ngtrader/utils/*`
- `src/__init__.py` -> `src/ngtrader/__init__.py`

2. Update imports:
- `from src.db ...` -> `from ngtrader.db ...`
- `from src.models ...` -> `from ngtrader.models ...`
- `from src.utils...` -> `from ngtrader.utils...`
- Includes scripts and Alembic env.

3. Update packaging target in `pyproject.toml`:
- from: `packages = ["src"]`
- to: `packages = ["src/ngtrader"]`

4. Keep `[build-system]` section intact to stay in installable mode.

## Rollout Plan

1. **Restructure package paths**
- Move files to `src/ngtrader/`.
- Ensure `__init__.py` exists in package directories.

2. **Refactor imports**
- Update all internal imports in `scripts/`, `alembic/`, and package modules.

3. **Update build config**
- Patch `pyproject.toml` package target to `src/ngtrader`.

4. **Verification gates**
- Run Ruff.
- Run Pyright.
- Manually run key scripts in dev environment:
  - DB setup
  - TWS connectivity
  - Position download

## Acceptance Criteria

- No remaining `from src...` imports in repository code.
- `uv sync` completes and app modules import as `ngtrader`.
- Alembic migration environment imports `ngtrader` modules successfully.
- Existing ops scripts run without behavior regressions after import path changes.
- Ruff and Pyright pass.

## Risks and Mitigations

- Import breakage in scripts or Alembic.
  - Mitigation: repo-wide search for `from src` and `import src`.
- Packaging target misconfigured.
  - Mitigation: verify `pyproject.toml` points to `src/ngtrader`.
- Hidden references in docs/commands.
  - Mitigation: update docs examples that mention module paths if present.

