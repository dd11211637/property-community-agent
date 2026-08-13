"""Fail when new production Python code exceeds repository structure limits."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
BASELINE_PATH = ROOT / "config" / "code_quality_baseline.json"
LIMITS = {"modules": 500, "functions": 80, "classes": 400}


def _line_count(node: ast.AST) -> int:
    return int(node.end_lineno) - int(node.lineno) + 1


def _walk_definitions(body: list[ast.stmt], prefix: str = "") -> list[tuple[str, ast.AST]]:
    definitions: list[tuple[str, ast.AST]] = []
    for node in body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        qualified_name = f"{prefix}.{node.name}" if prefix else node.name
        definitions.append((qualified_name, node))
        definitions.extend(_walk_definitions(node.body, qualified_name))
    return definitions


def _measure() -> dict[str, dict[str, int]]:
    measured = {kind: {} for kind in LIMITS}
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        measured["modules"][relative] = len(source.splitlines())
        tree = ast.parse(source, filename=str(path))
        for qualified_name, node in _walk_definitions(tree.body):
            key = f"{relative}:{qualified_name}"
            kind = "classes" if isinstance(node, ast.ClassDef) else "functions"
            measured[kind][key] = _line_count(node)
    return measured


def _check_limits(
    measured: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> list[str]:
    errors: list[str] = []
    for kind, limit in LIMITS.items():
        current = measured[kind]
        allowed_debt = baseline.get(kind, {})
        for key, size in current.items():
            if size <= limit:
                continue
            baseline_size = allowed_debt.get(key)
            if baseline_size is None:
                errors.append(f"new {kind[:-1]} limit violation: {key} is {size}, max {limit}")
            elif size > baseline_size:
                errors.append(
                    f"historical {kind[:-1]} grew: {key} is {size}, baseline {baseline_size}"
                )
        for key in allowed_debt:
            if current.get(key, 0) <= limit:
                errors.append(f"stale baseline entry must be removed: {key}")
    return errors


def _check_production_imports() -> list[str]:
    errors: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(
                name in {"tests", "testing"} or name.startswith(("tests.", "testing."))
                for name in modules
            ):
                relative = path.relative_to(ROOT).as_posix()
                errors.append(f"production imports test/demo code: {relative}:{node.lineno}")
    return errors


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    errors = _check_limits(_measure(), baseline) + _check_production_imports()
    if errors:
        print("Code structure check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Code structure check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
