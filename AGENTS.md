# OmniShip — Agent Guidelines & Repository Context

## Project Overview

OmniShip is a plugin-driven DAG release orchestration engine built in Python. Its core philosophy is:
> **Prove that Check → Build → Ship works as three composable DAGs, with plugins and artifacts flowing between them.**

## Core Architectural Invariants

Agents working on this codebase must adhere to the following architectural invariants:

1. **Strict Stage Boundaries**:
   - Stages execute sequentially: `CHECK` → `BUILD` → `SHIP`.
   - Stage completion barrier: all terminal nodes in a stage must succeed before the next stage starts.
   - **No cross-stage DAG edges**: nodes in `BUILD` cannot depend on individual nodes in `CHECK`.
2. **Core vs. Plugins Separation**:
   - `src/omniship/core/` contains pure DAG, node, stage, artifact, and executor models. It must know nothing about specific external integrations.
   - All integrations must be implemented as operations adhering to the `Operation` protocol (`src/omniship/plugins/api.py`).
   - Generic built-ins (`core/command`, `core/noop`, `core/python`) remain under `operations/`.
   - Tool-specific first-party providers (including Python and GitHub) live under `plugins/` and use the exact same entry-point registration protocol (`omniship.plugins`) as external third-party plugins.
3. **Failure & Skipping Semantics**:
   - If a node fails, directly and transitively dependent downstream nodes are marked `SKIPPED` (`Dependency '<id>' failed`).
   - Unrelated branches continue executing concurrently.
   - The stage is marked failed, preventing subsequent stages from starting.
4. **First-Class Artifacts**:
   - Artifacts produced during `BUILD` are verified on disk, normalized as `Artifact` objects, stored in `ExecutionContext.artifacts` (`ArtifactSet`), and forwarded to `SHIP` operations.
   - `CHECK` artifacts reach `BUILD` only from tasks declared with `forward_to_build=True`; this is a stage-level handoff, not a DAG edge.

---

## Directory Structure

```text
src/omniship/
├── core/             # Core DAG engine, nodes, artifacts, results, context, executor
│   ├── stage.py      # Stage enum (CHECK, BUILD, SHIP)
│   ├── node.py       # Node, NodeInputs, Dependency models
│   ├── artifact.py   # Artifact and ArtifactSet models
│   ├── result.py     # NodeResult and NodeStatus models
│   ├── context.py    # ExecutionContext
│   ├── graph.py      # StageGraph, cycle detection, topological levels
│   └── executor.py   # StageExecutor (asyncio-based DAG executor)
├── config/           # Configuration parsing and schema validation
│   ├── models.py     # Pydantic models (OmniShipConfig, NodeConfig)
│   ├── loader.py     # YAML loader & locator
│   └── validation.py # Validation of configs against registry & graph rules
├── plugins/          # Plugin framework plus bundled official providers
│   ├── api.py        # Operation and Plugin protocols
│   ├── metadata.py   # OperationDefinition
│   ├── registry.py   # PluginRegistry
│   ├── discovery.py  # Python entry-point loader
│   ├── template.py   # Schema/template generator
│   ├── python/       # Official Python blocks, operations, runtime helpers
│   └── github/       # Official GitHub block and release operation
├── operations/       # Generic built-in operations
│   ├── command.py    # core/command (arbitrary shell command)
│   ├── noop.py       # core/noop
│   └── python_task.py # core/python imperative task runner
└── cli/              # Click & Rich CLI interface
    ├── app.py        # CLI entry point
    ├── check.py      # 'omniship check' command
    ├── build.py      # 'omniship build' command
    ├── ship.py       # 'omniship ship' command
    ├── plan.py       # 'omniship plan' command
    ├── plugins.py    # 'omniship plugins' & 'omniship plugin' commands
    ├── runner.py     # Pipeline execution coordinator
    └── ui.py         # Rich terminal output handler
```

---

## Tooling & Commands

- **Package Manager**: `uv`
- **Install in editable mode**:
  ```bash
  uv pip install -e .
  ```
- **Run tests**:
  ```bash
  uv run pytest
  ```
- **Run CLI commands**:
  ```bash
  uv run omniship --help
  uv run omniship plan -c examples/multi-artifact/omniship.yaml
  uv run omniship check -c examples/multi-artifact/omniship.yaml
  uv run omniship build -c examples/multi-artifact/omniship.yaml
  uv run omniship ship -c examples/multi-artifact/omniship.yaml
  uv run omniship plugins
  uv run omniship plugin show core/command
  uv run omniship plugin template core/command
  ```

---

## Testing & Quality Guidelines

- When modifying or adding operations, core DAG logic, or config schemas, add matching tests under `tests/`:
  - `tests/core/`: Unit tests for graph validation, cycle detection, DAG executor, artifact flow.
  - `tests/config/`: Unit tests for config loading, validation, and invalid schemas.
  - `tests/plugins/`: Unit tests for registry, discovery, and templating.
  - `tests/integration/`: End-to-end CLI runs with CliRunner.
- Always run `uv run pytest` before completing tasks.
- Keep dependencies minimal; avoid bringing in unnecessary runtime dependencies.
