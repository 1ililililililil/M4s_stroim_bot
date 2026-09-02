---
name: Python setup in the pnpm Replit template
description: Environment notes for running an archived Python app in a pnpm-based Replit project.
---

The base Python module may not include pip; use the full Python tools module before installing `requirements.txt`. The package installer can append resolved packages to the requirements file, so preserve the archive's original requirements after installation when exact source fidelity matters.

**Why:** The workspace template is pnpm-based even when the requested application is Python, and the lightweight Python module does not provide the package manager needed for dependency installation.

**How to apply:** Configure the root run command in `.replit`, install dependencies through the language package manager, then verify the required archive files, imports, compilation, and tests.

In this workspace, invoke tests as `python -m pytest`; the standalone `pytest` launcher did not add the repository root to imports.

**Why:** The project modules imported correctly through Python, but standalone test collection failed with `ModuleNotFoundError: app`.

**How to apply:** Use `python -m pytest` for local and validation runs.

In the current pytest-asyncio strict mode, async fixtures must use `@pytest_asyncio.fixture`; `@pytest.fixture` is for synchronous fixtures only.

**Why:** Pytest otherwise reports that no plugin handled the async fixture, even when pytest-asyncio is installed.

**How to apply:** Import `pytest_asyncio` in test modules and decorate every `async def` fixture with `@pytest_asyncio.fixture`.