"""
Anthropic SDK wrapper.

Features:
- Prompt caching on system prompts (saves ~90% on repeat calls)
- Cost tracking per call (stored in agent_runs table)
- Model routing: "cheap" → Haiku 4.5, "smart" → Sonnet 4.6
- Structured output via tool_use (more reliable than JSON parsing)
- All calls are synchronous (Celery workers are sync)

Pricing (per 1M tokens):
  Haiku 4.5:   $1.00 input / $5.00 output  (cache read: $0.10 / write: $1.25)
  Sonnet 4.6:  $3.00 input / $15.00 output (cache read: $0.30 / write: $3.75)
"""

import hashlib
import logging
from typing import Any

import anthropic

from app.agents.openai_fallback import (
    call_plain_openai,
    call_with_optional_tools_openai,
    call_with_tool_openai,
    is_anthropic_balance_error,
)
from app.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

MODEL_MAP = {
    "cheap": settings.AI_DAILY_MODEL,     # claude-haiku-4-5-20251001
    "smart": settings.AI_COMPLEX_MODEL,   # claude-sonnet-4-6
}

# Cost per 1M tokens (USD)
PRICING = {
    settings.AI_DAILY_MODEL: {
        "input": 1.00, "output": 5.00,
        "cache_read": 0.10, "cache_write": 1.25,
    },
    settings.AI_COMPLEX_MODEL: {
        "input": 3.00, "output": 15.00,
        "cache_read": 0.30, "cache_write": 3.75,
    },
}


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {
            "api_key": settings.ANTHROPIC_API_KEY,
            "timeout": 120.0,  # extend timeout for long Sonnet calls via Vercel proxy
            "max_retries": 2,
        }
        # Use Cloudflare Worker / Vercel proxy if configured (bypasses geo-block)
        if settings.ANTHROPIC_BASE_URL:
            kwargs["base_url"] = settings.ANTHROPIC_BASE_URL
        _client = anthropic.Anthropic(**kwargs)
    return _client


def _compute_cost(model: str, usage: anthropic.types.Usage) -> float:
    p = PRICING.get(model, PRICING[settings.AI_DAILY_MODEL])
    cost = (
        (usage.input_tokens / 1_000_000) * p["input"]
        + (usage.output_tokens / 1_000_000) * p["output"]
    )
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost += (cache_creation / 1_000_000) * p["cache_write"]
    cost += (cache_read / 1_000_000) * p["cache_read"]
    return round(cost, 8)


def _prompt_hash(system: str, messages: list[dict]) -> str:
    content = system + str(messages[:1])
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def call_with_tool(
    *,
    model_tier: str = "cheap",
    system: str,
    user_message: str,
    tool: dict[str, Any],
    max_tokens: int = 4096,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Call Claude with a single tool and force it to use that tool.
    Returns (tool_input_dict, usage_stats).

    Uses streaming so long Sonnet generations don't trip the Vercel
    proxy's single-request timeout (~78s) — the proxy sees bytes flowing
    and keeps the connection alive end-to-end. Prompt caching on the
    system prompt is preserved. Output is still structured via tool_use.
    """
    client = get_client()
    model = MODEL_MAP.get(model_tier, MODEL_MAP["cheap"])

    system_blocks = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": user_message}]

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001
        if is_anthropic_balance_error(exc):
            logger.warning("Anthropic balance exhausted — falling back to OpenAI")
            return call_with_tool_openai(
                model_tier=model_tier,
                system=system,
                user_message=user_message,
                tool=tool,
                max_tokens=max_tokens,
            )
        raise

    usage = response.usage
    cost = _compute_cost(model, usage)

    usage_stats = {
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cost_usd": cost,
        "prompt_hash": _prompt_hash(system, messages),
        "provider": "anthropic",
    }

    logger.info(
        "LLM call: model=%s tokens=%d+%d cost=$%.5f cache_read=%d",
        model,
        usage.input_tokens,
        usage.output_tokens,
        cost,
        usage_stats["cache_read_tokens"],
    )

    tool_input = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            tool_input = block.input
            break

    return tool_input, usage_stats


def call_plain(
    *,
    model_tier: str = "cheap",
    system: str,
    user_message: str,
    max_tokens: int = 1500,
) -> tuple[str, dict[str, Any]]:
    """Free-form chat call — returns the concatenated text content and usage.

    Uses streaming like `call_with_tool` so long Sonnet generations
    survive the Vercel proxy's single-request timeout. Prompt caching is
    still applied to the system block.
    """
    client = get_client()
    model = MODEL_MAP.get(model_tier, MODEL_MAP["cheap"])

    system_blocks = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": user_message}]

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001
        if is_anthropic_balance_error(exc):
            logger.warning("Anthropic balance exhausted — falling back to OpenAI")
            return call_plain_openai(
                model_tier=model_tier,
                system=system,
                user_message=user_message,
                max_tokens=max_tokens,
            )
        raise

    usage = response.usage
    cost = _compute_cost(model, usage)
    stop_reason = getattr(response, "stop_reason", None)
    usage_stats = {
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cost_usd": cost,
        "prompt_hash": _prompt_hash(system, messages),
        "stop_reason": stop_reason,
        "truncated": stop_reason == "max_tokens",
        "provider": "anthropic",
    }

    logger.info(
        "LLM call (plain): model=%s tokens=%d+%d cost=$%.5f cache_read=%d stop=%s",
        model,
        usage.input_tokens,
        usage.output_tokens,
        cost,
        usage_stats["cache_read_tokens"],
        stop_reason,
    )

    text_parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    return "".join(text_parts), usage_stats


def call_with_optional_tools(
    *,
    model_tier: str = "cheap",
    system: str,
    user_message: str,
    tools: list[dict[str, Any]],
    max_tokens: int = 1500,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mixed-mode call: model may answer with plain text OR pick a
    tool. We don't force `tool_choice` — `auto` lets the model decide.

    Returns ({"text": str | None, "tool_use": {"name": str, "input": dict} | None}, usage_stats).
    Caller branches on whichever arrived. Streaming for the same proxy-
    timeout reasons as `call_plain`.
    """
    client = get_client()
    model = MODEL_MAP.get(model_tier, MODEL_MAP["cheap"])

    system_blocks = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = [{"role": "user", "content": user_message}]

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            tools=tools,
            # `auto`: model chooses. We DON'T set tool_choice={"type":"tool",...}
            # because that forces a tool every time — owner's normal questions
            # should still get a plain answer.
            tool_choice={"type": "auto"},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001
        if is_anthropic_balance_error(exc):
            logger.warning("Anthropic balance exhausted — falling back to OpenAI")
            return call_with_optional_tools_openai(
                model_tier=model_tier,
                system=system,
                user_message=user_message,
                tools=tools,
                max_tokens=max_tokens,
            )
        raise

    usage = response.usage
    cost = _compute_cost(model, usage)
    # Stop reason matters for chat: `max_tokens` means the model was cut
    # off mid-thought. Without surfacing this to caller, the truncated
    # text would be saved as a "normal" assistant turn and feed back
    # into the next prompt as if it were a complete answer.
    stop_reason = getattr(response, "stop_reason", None)
    usage_stats = {
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cost_usd": cost,
        "prompt_hash": _prompt_hash(system, messages),
        "stop_reason": stop_reason,
        "truncated": stop_reason == "max_tokens",
        "provider": "anthropic",
    }

    text_parts: list[str] = []
    tool_use: dict[str, Any] | None = None
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            # Take the first tool_use; ignore subsequent ones (we
            # don't ask for parallel tools and Anthropic streaming
            # rarely emits more than one anyway).
            if tool_use is None:
                tool_use = {"name": block.name, "input": dict(block.input)}

    text = "".join(text_parts) or None
    logger.info(
        "LLM call (optional-tools): model=%s tokens=%d+%d cost=$%.5f tool=%s stop=%s",
        model, usage.input_tokens, usage.output_tokens, cost,
        tool_use["name"] if tool_use else "none",
        stop_reason,
    )
    return {"text": text, "tool_use": tool_use}, usage_stats
