"""Provider-neutral packages must not depend on the GitHub provider."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "omniship"
NEUTRAL_PACKAGES = ("core", "runtime", "workflow", "config", "operations", "cli")
GITHUB_MODULE = "omniship.plugins.github"


def _neutral_modules() -> list[Path]:
    return sorted(
        path
        for package in NEUTRAL_PACKAGES
        for path in (SOURCE / package).rglob("*.py")
    )


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


@pytest.mark.parametrize(
    "path",
    _neutral_modules(),
    ids=lambda path: str(path.relative_to(SOURCE)),
)
def test_neutral_package_does_not_know_about_github(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    github_imports = sorted(
        module
        for module in _imported_modules(tree)
        if module == GITHUB_MODULE or module.startswith(f"{GITHUB_MODULE}.")
    )
    github_variables = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("GITHUB_")
    )

    assert github_imports == []
    assert github_variables == []
