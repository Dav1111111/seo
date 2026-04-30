"""Strategic focus — owner-set lens for the whole Studio.

Studio v2 etap 7 Phase E.

Problem: the brain treats all directions equally. On grandtourspirit
the owner cares ONLY about багги-Абхазия right now; яхты, вертолёты,
Крым are noise until later. Without a way to record that, every
suggestion stays generic.

Solution: a single JSONB slot at `sites.target_config.strategic_focus`
storing one structured focus per site. Read-only for the chat; written
either via /studio/profile UI (manual) or via chat-flow-with-confirm
(LLM proposes → owner clicks «Применить»).

Storage shape (this module owns the contract):

    {
      "label": str,                # "Багги-экспедиции в Абхазию"
      "active_since": str,         # ISO timestamp, set by server
      "set_by": str,               # "owner_via_ui" | "owner_via_chat"
      "products": list[str],       # ["багги-экспедиции"]
      "regions": list[str],        # ["абхазия"]
      "query_signals": list[str],  # ["багги абхазия", "экскурсии абхазия"]
      "deprioritised": list[str],  # ["яхты", "вертолёты", "крым"]
      "exit_criterion": str|None,  # "топ-10 по «экскурсии абхазия»"
      "owner_note": str|None,      # free text from owner
      "deadline": str|None,        # ISO date, optional
    }

Validation rules (kept here so UI and chat-tool share them):
  - `label` is required, non-empty, ≤ 200 chars.
  - At least ONE of {products, regions, query_signals} non-empty —
    otherwise the focus is a no-op (nothing for downstream rules to
    match against).
  - List fields capped (PROducts 8, regions 8, query_signals 20,
    deprioritised 12) so a runaway LLM proposal can't bloat
    target_config.
  - All strings stripped + lowercased on save (matching normaliser
    used elsewhere in the project for consistency with target_config
    geo / services).

Universal: this module never references «багги», «Абхазия» or any
other domain-specific term. All real values come from per-site DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


MAX_LABEL_LEN = 200
MAX_PRODUCTS = 8
MAX_REGIONS = 8
MAX_QUERY_SIGNALS = 20
MAX_DEPRIORITISED = 12
MAX_NOTE_LEN = 1000
MAX_EXIT_CRITERION_LEN = 500


class FocusValidationError(ValueError):
    """Raised when an incoming focus payload fails the contract."""


@dataclass
class StrategicFocus:
    """Validated, normalised view of a strategic focus."""
    label: str
    active_since: str        # ISO 8601, server-set
    set_by: str              # "owner_via_ui" | "owner_via_chat"
    products: list[str]
    regions: list[str]
    query_signals: list[str]
    deprioritised: list[str]
    exit_criterion: str | None
    owner_note: str | None
    deadline: str | None

    def to_jsonb(self) -> dict[str, Any]:
        """Shape we persist into target_config.strategic_focus."""
        return {
            "label": self.label,
            "active_since": self.active_since,
            "set_by": self.set_by,
            "products": self.products,
            "regions": self.regions,
            "query_signals": self.query_signals,
            "deprioritised": self.deprioritised,
            "exit_criterion": self.exit_criterion,
            "owner_note": self.owner_note,
            "deadline": self.deadline,
        }


def _norm_list(
    values: Any, *, cap: int, lowercase: bool = True,
) -> list[str]:
    """Strip, dedupe, optionally lowercase, cap to `cap` items.

    Accepts list[str] or anything iterable of stringifiable items;
    silently skips non-strings rather than raising — owner UI lets
    them paste comma-separated text and we want the lenient path.
    """
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if lowercase:
            s = s.lower()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _norm_text(value: Any, *, cap: int) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    return s[:cap]


def validate_and_normalise(
    payload: dict[str, Any],
    *,
    set_by: str,
    active_since: str | None = None,
) -> StrategicFocus:
    """Validate an incoming focus dict (from UI form OR LLM proposal)
    and return a StrategicFocus dataclass ready to persist.

    Raises FocusValidationError on hard contract violations:
      - missing/empty label
      - none of products/regions/query_signals populated

    Other lenient cleanups (over-cap lists, junk in optional fields)
    are silently dropped.
    """
    if set_by not in ("owner_via_ui", "owner_via_chat"):
        raise FocusValidationError(
            f"set_by must be 'owner_via_ui' or 'owner_via_chat', got {set_by!r}",
        )

    label_raw = payload.get("label")
    if not isinstance(label_raw, str):
        raise FocusValidationError("label is required (string)")
    label = label_raw.strip()
    if not label:
        raise FocusValidationError("label cannot be empty")
    if len(label) > MAX_LABEL_LEN:
        label = label[:MAX_LABEL_LEN]

    products = _norm_list(payload.get("products"), cap=MAX_PRODUCTS)
    regions = _norm_list(payload.get("regions"), cap=MAX_REGIONS)
    query_signals = _norm_list(
        payload.get("query_signals"), cap=MAX_QUERY_SIGNALS,
    )
    deprioritised = _norm_list(
        payload.get("deprioritised"), cap=MAX_DEPRIORITISED,
    )

    if not (products or regions or query_signals):
        raise FocusValidationError(
            "focus must specify at least one product, region or query "
            "signal — otherwise downstream rules have nothing to match",
        )

    exit_criterion = _norm_text(
        payload.get("exit_criterion"), cap=MAX_EXIT_CRITERION_LEN,
    )
    owner_note = _norm_text(payload.get("owner_note"), cap=MAX_NOTE_LEN)
    deadline_raw = payload.get("deadline")
    deadline: str | None = None
    if isinstance(deadline_raw, str) and deadline_raw.strip():
        # Accept «2026-06-30» or full ISO; we don't parse strictly,
        # just trim and pass through. UI provides a date picker.
        deadline = deadline_raw.strip()[:32]

    return StrategicFocus(
        label=label,
        active_since=active_since or datetime.now(timezone.utc).isoformat(),
        set_by=set_by,
        products=products,
        regions=regions,
        query_signals=query_signals,
        deprioritised=deprioritised,
        exit_criterion=exit_criterion,
        owner_note=owner_note,
        deadline=deadline,
    )


def from_target_config(target_config: dict[str, Any] | None) -> StrategicFocus | None:
    """Read the focus slot back out of target_config. Returns None
    when the site has no focus set."""
    raw = (target_config or {}).get("strategic_focus")
    if not isinstance(raw, dict):
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    return StrategicFocus(
        label=str(label),
        active_since=str(raw.get("active_since") or ""),
        set_by=str(raw.get("set_by") or "owner_via_ui"),
        products=[str(p) for p in (raw.get("products") or []) if p],
        regions=[str(p) for p in (raw.get("regions") or []) if p],
        query_signals=[str(p) for p in (raw.get("query_signals") or []) if p],
        deprioritised=[str(p) for p in (raw.get("deprioritised") or []) if p],
        exit_criterion=raw.get("exit_criterion") or None,
        owner_note=raw.get("owner_note") or None,
        deadline=raw.get("deadline") or None,
    )


def render_for_prompt(focus: StrategicFocus | None) -> str:
    """Compact human-readable block for inclusion in chat prompts.
    When focus is None, returns a clear «no focus» line so the LLM
    knows to give general advice instead of forcing one."""
    if focus is None:
        return (
            "ТЕКУЩИЙ ФОКУС: не задан. Владелец пока не указал главное "
            "направление. Если по контексту разговора станет понятно, "
            "какой фокус нужен — предложи его через инструмент "
            "(propose_strategic_focus), не пиши свой текст напрямую."
        )

    parts = [
        "ТЕКУЩИЙ ФОКУС (всё, что ты советуешь, должно быть подчинено ему):",
        f"  главное: {focus.label}",
    ]
    if focus.products:
        parts.append("  продукты в фокусе: " + ", ".join(focus.products))
    if focus.regions:
        parts.append("  регионы в фокусе: " + ", ".join(focus.regions))
    if focus.query_signals:
        parts.append(
            "  ключевые запросы: " + ", ".join(focus.query_signals[:10]),
        )
    if focus.deprioritised:
        parts.append(
            "  отложено (не предлагать сейчас): "
            + ", ".join(focus.deprioritised),
        )
    if focus.exit_criterion:
        parts.append(f"  условие выхода из фокуса: {focus.exit_criterion}")
    if focus.deadline:
        parts.append(f"  дедлайн: {focus.deadline}")
    if focus.owner_note:
        parts.append(f"  заметка владельца: {focus.owner_note}")
    parts.append(
        "  с момента: " + (focus.active_since or "неизвестно"),
    )
    parts.append(
        "  ⚠ Если владелец просит совет вне фокуса — мягко напомни, "
        "что фокус сейчас другой, и предложи вернуться к нему. "
        "Не игнорируй вопрос, но привяжи ответ к фокусу.",
    )
    return "\n".join(parts)


__all__ = [
    "FocusValidationError",
    "StrategicFocus",
    "MAX_LABEL_LEN",
    "MAX_PRODUCTS",
    "MAX_REGIONS",
    "MAX_QUERY_SIGNALS",
    "MAX_DEPRIORITISED",
    "MAX_NOTE_LEN",
    "MAX_EXIT_CRITERION_LEN",
    "validate_and_normalise",
    "from_target_config",
    "render_for_prompt",
]
