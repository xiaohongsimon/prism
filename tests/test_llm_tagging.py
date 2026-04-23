"""AST regression: every call_llm/call_llm_json must pass task= kwarg.

Why this exists: the LLM wrapper tags each outbound call with task/scope so the
token-tracker dashboard can group spend by transformation type. If a new call
site forgets `task=`, the wrapper raises TypeError at runtime — this test
catches the mistake at commit time instead.

Scope: walks prism/ source AST, finds every Call node whose function name is
`call_llm` or `call_llm_json`, asserts the `task=` keyword is present. The
wrapper definition itself (prism/pipeline/llm.py) is excluded because its
body contains the only legitimate untagged forwarding logic.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PRISM_ROOT = Path(__file__).resolve().parent.parent / "prism"
WRAPPER_FILE = PRISM_ROOT / "pipeline" / "llm.py"
TAGGED_FUNCS = {"call_llm", "call_llm_json"}


def _iter_source_files() -> list[Path]:
    return [p for p in PRISM_ROOT.rglob("*.py") if p != WRAPPER_FILE]


def _missing_task_kwarg_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (func_name, lineno) for calls missing task= kwarg."""
    problems: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
        else:
            continue
        if name not in TAGGED_FUNCS:
            continue
        kw_names = {kw.arg for kw in node.keywords if kw.arg is not None}
        if "task" not in kw_names:
            problems.append((name, node.lineno))
    return problems


def test_every_llm_call_site_has_task_kwarg() -> None:
    failures: list[str] = []
    for path in _iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            pytest.fail(f"syntax error in {path}: {exc}")
        for func_name, lineno in _missing_task_kwarg_calls(tree):
            rel = path.relative_to(PRISM_ROOT.parent)
            failures.append(f"{rel}:{lineno} — {func_name}(...) missing task= kwarg")

    assert not failures, (
        "LLM call sites must pass task=Task.XXX so the token-tracker dashboard "
        "can group spend by transformation type. Offenders:\n  "
        + "\n  ".join(failures)
    )
