"""Parity test — fixtures re-run against CURRENT code must match baseline.json.

Run after every refactor step:  pytest backend/tests/golden/test_parity.py
If this test fails, the refactor broke behavior. Revert until it passes.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.golden.capture_baseline import (
    capture_pages,
    capture_queries,
    capture_standalone,
)

BASELINE_PATH = Path(__file__).parent / "baseline.json"


def _load_baseline() -> dict:
    assert BASELINE_PATH.exists(), (
        "baseline.json missing — run `python -m tests.golden.capture_baseline` first"
    )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_queries_parity():
    baseline = _load_baseline()
    current = capture_queries()
    assert current == baseline["queries"], (
        f"Query classification drift: {json.dumps(current, ensure_ascii=False, indent=2)}"
    )


def test_pages_parity():
    baseline = _load_baseline()
    current = capture_pages()
    assert current == baseline["pages"], (
        f"Page scoring drift: {json.dumps(current, ensure_ascii=False, indent=2)}"
    )


def test_standalone_parity():
    baseline = _load_baseline()
    current = capture_standalone()
    assert current == baseline["standalone"], (
        f"Standalone test drift: {json.dumps(current, ensure_ascii=False, indent=2)}"
    )
