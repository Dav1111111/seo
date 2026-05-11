"""OpenAI fallback for when Anthropic is unavailable.

Triggered automatically by `llm_client` when an Anthropic call fails
with `BadRequestError` mentioning "credit balance" — i.e. the
Anthropic balance ran dry. Mirrors the three call shapes
(`call_plain`, `call_with_tool`, `call_with_optional_tools`) and
returns the same `(payload, usage_stats)` tuple so callers don't need
to change.

Models:
    cheap → gpt-4o-mini   (input $0.15 / output $0.60 per 1M)
    smart → gpt-4o        (input $2.50 / output $10.00 per 1M)

Tool / function calling: Anthropic's `tool` dict has shape
``{"name", "description", "input_schema"}``; OpenAI wants
``{"type": "function", "function": {"name", "description", "parameters"}}``.
We translate on the fly so the same `tool` literal works for both.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

OPENAI_MODEL_MAP = {
    "cheap": "gpt-5.4-mini",
    "smart": "gpt-5.4",
}

# 5.x models require max_completion_tokens; 4.x accept max_tokens.
# We pick the right field per call; older models stay as fallback if
# someone overrides OPENAI_MODEL_MAP env-side.
_USES_COMPLETION_TOKENS_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _max_tokens_kwarg(model: str, value: int) -> dict[str, int]:
    """Return the right max-tokens kwarg for the given model."""
    if any(model.startswith(p) for p in _USES_COMPLETION_TOKENS_PREFIXES):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


# Pricing per 1M tokens (as of 2026-05). Approximate — refresh from
# platform.openai.com/pricing if numbers drift.
OPENAI_PRICING = {
    "gpt-5.4":      {"input": 2.50,  "output": 10.00},
    "gpt-5.4-mini": {"input": 0.25,  "output": 2.00},
    "gpt-5.4-nano": {"input": 0.05,  "output": 0.40},
    "gpt-5.4-pro":  {"input": 15.00, "output": 60.00},
    "gpt-5.5":      {"input": 3.00,  "output": 12.00},
    "gpt-5":        {"input": 2.50,  "output": 10.00},
    "gpt-5-mini":   {"input": 0.25,  "output": 2.00},
    "gpt-5-nano":   {"input": 0.05,  "output": 0.40},
    "gpt-4o":       {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":  {"input": 0.15,  "output": 0.60},
}


def get_openai_client() -> OpenAI:
    """Lazy singleton — only created if a fallback ever fires."""
    global _client
    if _client is None:
        api_key = getattr(settings, "OPENAI_API_KEY", None) or ""
        if not api_key:
            raise RuntimeError(
                "OpenAI fallback requested but OPENAI_API_KEY is not set"
            )
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": 120.0,
            "max_retries": 2,
        }
        # Optional reverse-proxy (Cloudflare Worker / Vercel) — required
        # when the host is geo-blocked (e.g. Jino in RU).
        base_url = getattr(settings, "OPENAI_BASE_URL", None) or ""
        if base_url:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)
    return _client


def _to_openai_function(tool: dict[str, Any]) -> dict[str, Any]:
    """Translate Anthropic-style tool dict → OpenAI function dict."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or tool.get("parameters") or {},
        },
    }


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    # Strip dated suffix (e.g. "gpt-5.4-mini-2026-03-17" → "gpt-5.4-mini").
    base = model
    for suffix_start in ("-2024", "-2025", "-2026"):
        if suffix_start in base:
            base = base.split(suffix_start)[0]
            break
    p = OPENAI_PRICING.get(base) or OPENAI_PRICING.get(model) or OPENAI_PRICING["gpt-5.4-mini"]
    return round(
        (prompt_tokens / 1_000_000) * p["input"]
        + (completion_tokens / 1_000_000) * p["output"],
        8,
    )


def _build_usage(model: str, response, stop_reason: str | None = None) -> dict[str, Any]:
    usage = response.usage
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    return {
        "model": f"openai:{model}",
        "input_tokens": pt,
        "output_tokens": ct,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": _compute_cost(model, pt, ct),
        "prompt_hash": "",
        "stop_reason": stop_reason or response.choices[0].finish_reason,
        "truncated": response.choices[0].finish_reason == "length",
        "provider": "openai",
    }


def call_plain_openai(
    *,
    model_tier: str,
    system: str,
    user_message: str,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    client = get_openai_client()
    model = OPENAI_MODEL_MAP.get(model_tier, OPENAI_MODEL_MAP["cheap"])

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        **_max_tokens_kwarg(model, max_tokens),
    )
    text = response.choices[0].message.content or ""
    usage_stats = _build_usage(model, response)
    logger.info(
        "OpenAI fallback (plain): model=%s tokens=%d+%d cost=$%.5f",
        model, usage_stats["input_tokens"], usage_stats["output_tokens"],
        usage_stats["cost_usd"],
    )
    return text, usage_stats


def call_with_tool_openai(
    *,
    model_tier: str,
    system: str,
    user_message: str,
    tool: dict[str, Any],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = get_openai_client()
    model = OPENAI_MODEL_MAP.get(model_tier, OPENAI_MODEL_MAP["cheap"])
    fn = _to_openai_function(tool)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        tools=[fn],
        tool_choice={"type": "function", "function": {"name": tool["name"]}},
        **_max_tokens_kwarg(model, max_tokens),
    )

    tool_input: dict[str, Any] = {}
    msg = response.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        try:
            tool_input = json.loads(tool_calls[0].function.arguments or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("OpenAI fallback: tool args parse failed: %s", exc)

    usage_stats = _build_usage(model, response)
    logger.info(
        "OpenAI fallback (tool=%s): model=%s tokens=%d+%d cost=$%.5f",
        tool["name"], model,
        usage_stats["input_tokens"], usage_stats["output_tokens"],
        usage_stats["cost_usd"],
    )
    return tool_input, usage_stats


def call_with_optional_tools_openai(
    *,
    model_tier: str,
    system: str,
    user_message: str,
    tools: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = get_openai_client()
    model = OPENAI_MODEL_MAP.get(model_tier, OPENAI_MODEL_MAP["cheap"])
    fns = [_to_openai_function(t) for t in tools]

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        tools=fns if fns else None,
        tool_choice="auto" if fns else None,
        **_max_tokens_kwarg(model, max_tokens),
    )

    msg = response.choices[0].message
    text = (msg.content or None) if msg.content else None
    tool_use: dict[str, Any] | None = None
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        first = tool_calls[0]
        try:
            args = json.loads(first.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_use = {"name": first.function.name, "input": args}

    usage_stats = _build_usage(model, response)
    logger.info(
        "OpenAI fallback (optional-tools): model=%s tokens=%d+%d cost=$%.5f tool=%s",
        model, usage_stats["input_tokens"], usage_stats["output_tokens"],
        usage_stats["cost_usd"], tool_use["name"] if tool_use else "none",
    )
    return {"text": text, "tool_use": tool_use}, usage_stats


def is_anthropic_balance_error(exc: BaseException) -> bool:
    """Detect the specific Anthropic billing error vs other 400s.

    We DON'T want to fallback on every BadRequestError — a malformed
    tool spec is our bug, not a money issue, and silently re-routing
    would hide the regression. Match on the explicit credit-balance
    message Anthropic uses.
    """
    try:
        import anthropic
    except ImportError:
        return False
    if not isinstance(exc, anthropic.BadRequestError):
        return False
    return "credit balance" in str(exc).lower()


__all__ = [
    "call_plain_openai",
    "call_with_optional_tools_openai",
    "call_with_tool_openai",
    "is_anthropic_balance_error",
]
