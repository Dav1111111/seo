"""Schema-audit integration tests for studio.py.

Covers the three studio.py touch points:
  1. `DeepExtractRow.schema_audit` is populated by `_row_to_extract`.
  2. `_row_to_extract` is fail-soft when `audit_schema` raises.
  3. `_format_extract_for_llm` emits the structured SCHEMA AUDIT
     block with the «ОБЯЗАТЕЛЬНО: Используй ТОЛЬКО эти» guard.

We stub out the `app.core_audit.schema_audit` module via
`sys.modules` so the tests are independent of whether the real
detector has shipped yet (the schema_audit package is being built on a
parallel branch).
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.v1 import studio


# ── Shared helpers ──────────────────────────────────────────────────


class _FakeAuditResult:
    """Stand-in for `SchemaAuditResult` — only `.to_dict()` is used."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


def _install_fake_audit_module(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """Inject a fake `app.core_audit.schema_audit` into sys.modules.

    The real module may not exist yet (parallel-agent work). Tests are
    self-contained — we don't depend on the detector implementation.
    """
    fake = types.ModuleType("app.core_audit.schema_audit")

    def audit_schema(*, schema_blocks, full_text, url, title, h1):
        return _FakeAuditResult(payload)

    fake.audit_schema = audit_schema  # type: ignore[attr-defined]
    fake.SchemaAuditResult = _FakeAuditResult  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core_audit.schema_audit", fake)


def _install_raising_audit_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake module whose audit_schema raises."""
    fake = types.ModuleType("app.core_audit.schema_audit")

    def audit_schema(**_kwargs):
        raise RuntimeError("boom from fake audit_schema")

    fake.audit_schema = audit_schema  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core_audit.schema_audit", fake)


def _make_orm_like_row(**overrides):
    """Build a minimal stand-in for PageDeepExtract ORM row."""
    defaults = dict(
        id="00000000-0000-0000-0000-000000000001",
        url="https://example.com/page",
        is_competitor=False,
        competitor_domain=None,
        status="completed",
        error=None,
        extracted_at=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
        duration_ms=1500,
        title="Демо страница",
        h1="Демо H1",
        meta_description="Демо meta",
        headings_tree=[],
        cta_inventory=[],
        forms_inventory=[],
        images_inventory=[],
        css_palette=[],
        fonts=[],
        layout_meta={},
        performance={"lcp": 1200, "fcp": 700, "cls": 0},
        js_errors=[],
        schema_blocks=[{"__format": "json-ld", "@type": "Organization"}],
        full_text="Видимый текст страницы.",
        screenshot_desktop_path=None,
        screenshot_mobile_path=None,
        ai_summary_md=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── Tests ───────────────────────────────────────────────────────────


def test_deep_extract_row_includes_schema_audit_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_row_to_extract` exposes the audit dict on the response model."""
    payload = {
        "detected_types": ["Organization"],
        "formats": ["json-ld"],
        "valid_blocks_count": 1,
        "parse_error_count": 0,
        "issues": [
            {
                "code": "schema.organization.no_sameas",
                "severity": "info",
                "message_ru": "В Organization не указан sameAs.",
                "evidence": "block #1",
                "fix_ru": "Добавьте массив sameAs со ссылками на соцсети.",
                "source": "detector",
            }
        ],
        "recommendations": ["Добавьте sameAs"],
        "summary_ru": "1 блок, 0 parse errors.",
    }
    _install_fake_audit_module(monkeypatch, payload)

    row = _make_orm_like_row()
    result = studio._row_to_extract(row)

    assert result.schema_audit is not None
    assert isinstance(result.schema_audit, dict)
    # All advertised keys round-trip:
    for key in (
        "detected_types",
        "formats",
        "valid_blocks_count",
        "parse_error_count",
        "issues",
        "recommendations",
        "summary_ru",
    ):
        assert key in result.schema_audit, f"missing key: {key}"
    assert result.schema_audit["detected_types"] == ["Organization"]
    assert result.schema_audit["valid_blocks_count"] == 1
    assert result.schema_audit["issues"][0]["code"] == "schema.organization.no_sameas"


def test_row_to_extract_failsafe_on_audit_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `audit_schema` raises, the row is still built with `schema_audit=None`."""
    _install_raising_audit_module(monkeypatch)

    row = _make_orm_like_row()
    result = studio._row_to_extract(row)

    # The endpoint must NOT 500 — we get a valid row back, just without audit.
    assert result is not None
    assert result.url == "https://example.com/page"
    assert result.schema_audit is None


def test_format_extract_for_llm_includes_audit_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM payload contains the structured SCHEMA AUDIT block + guardrails."""
    payload = {
        "detected_types": ["Organization", "BlogPosting", "BreadcrumbList"],
        "formats": ["json-ld"],
        "valid_blocks_count": 4,
        "parse_error_count": 0,
        "issues": [
            {
                "code": "schema.parse_error",
                "severity": "critical",
                "message_ru": "Невалидный JSON-LD блок.",
                "evidence": "block #2 line 4",
                "fix_ru": "Замените одинарные кавычки на двойные.",
                "source": "detector",
            },
            {
                "code": "schema.offer.no_currency",
                "severity": "warning",
                "message_ru": "Offer без валюты.",
                "evidence": "Offer @price=1000",
                "fix_ru": "Добавьте priceCurrency: RUB.",
                "source": "detector",
            },
        ],
        "recommendations": [],
        "summary_ru": "Есть 1 критическая и 1 warning.",
    }
    _install_fake_audit_module(monkeypatch, payload)

    extract = SimpleNamespace(
        url="https://example.com/page",
        is_competitor=False,
        js_errors=[],
        title="Title",
        h1="H1",
        meta_description="Meta",
        full_text="Visible text",
        headings_tree=[],
        performance={"lcp": 1200, "fcp": 700, "cls": 0},
        layout_meta={"viewport_w": 1280, "viewport_h": 800, "doc_height": 1400},
        cta_inventory=[],
        forms_inventory=[],
        images_inventory=[],
        css_palette=[],
        fonts=[],
        schema_blocks=[
            {"__format": "json-ld", "@type": "Organization"},
        ],
        # Force the function to recompute via the fake module rather
        # than picking up a pre-set `schema_audit` attribute.
        schema_audit=None,
    )

    text = studio._format_extract_for_llm(extract)

    # Header is present.
    assert "=== SCHEMA AUDIT (детектор Python, не LLM) ===" in text
    # Detected types + format + counts are rendered.
    assert "Organization" in text
    assert "BlogPosting" in text
    assert "json-ld" in text
    assert "Найдено блоков: 4" in text
    assert "parse errors: 0" in text
    # Issue lines (severity + code + message + fix).
    assert "[critical] schema.parse_error" in text
    assert "[warning] schema.offer.no_currency" in text
    assert "Замените одинарные кавычки" in text
    # The guardrails block is the whole point of this change.
    assert "ОБЯЗАТЕЛЬНО:" in text
    assert "Используй ТОЛЬКО эти" in text
    assert "НЕ выдумывай Schema-проблемы" in text


def test_format_extract_for_llm_no_schema_no_block_explosion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty schema_blocks: audit summary present, no traceback / no crash."""
    payload = {
        "detected_types": [],
        "formats": [],
        "valid_blocks_count": 0,
        "parse_error_count": 0,
        "issues": [],
        "recommendations": [],
        "summary_ru": "Schema.org не найдена.",
    }
    _install_fake_audit_module(monkeypatch, payload)

    extract = SimpleNamespace(
        url="https://example.com/empty",
        is_competitor=False,
        js_errors=[],
        title="T",
        h1="H",
        meta_description="M",
        full_text="",
        headings_tree=[],
        performance={"lcp": None, "fcp": None, "cls": None},
        layout_meta={},
        cta_inventory=[],
        forms_inventory=[],
        images_inventory=[],
        css_palette=[],
        fonts=[],
        schema_blocks=[],
        schema_audit=None,
    )

    text = studio._format_extract_for_llm(extract)

    # The audit block IS present (because audit returned an OK-ish dict).
    assert "=== SCHEMA AUDIT" in text
    # With no issues, we still emit the "no issues" marker — not a traceback.
    assert "ISSUES: — (audit чист / нечего исправлять)" in text
    assert "Traceback" not in text
    # Guardrails still emitted so the LLM doesn't invent Schema items.
    assert "ОБЯЗАТЕЛЬНО:" in text
