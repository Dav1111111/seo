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
        kwargs: dict[str, Any] = {"api_key": settings.ANTHROPIC_API_KEY}
        # Use Cloudflare Worker proxy if configured (bypasses geo-block)
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

    Uses prompt caching on the system prompt automatically.
    Structured output via tool_use is more reliable than JSON parsing.
    """
    client = get_client()
    model = MODEL_MAP.get(model_tier, MODEL_MAP["cheap"])

    # Cache system prompt (saves ~90% on repeated calls with same system)
    system_blocks = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages = [{"role": "user", "content": user_message}]

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=messages,
    )

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
    }

    logger.info(
        "LLM call: model=%s tokens=%d+%d cost=$%.5f cache_read=%d",
        model,
        usage.input_tokens,
        usage.output_tokens,
        cost,
        usage_stats["cache_read_tokens"],
    )

    # Extract tool use result
    tool_input = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            tool_input = block.input
            break

    return tool_input, usage_stats
