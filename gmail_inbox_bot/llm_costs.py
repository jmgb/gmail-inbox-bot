"""LLM usage extraction and cost calculation helpers."""

from __future__ import annotations

from typing import TypedDict


class ModelPrice(TypedDict):
    """Price per million tokens."""

    input: float
    output: float
    provider: str


MODEL_PRICING: dict[str, ModelPrice] = {
    "gpt-5.1-2025-11-13": {"input": 1.25, "output": 10.00, "provider": "OpenAI"},
    "gpt-5.2-2025-12-11": {"input": 1.75, "output": 14.00, "provider": "OpenAI"},
    "gpt-5.4-2026-03-05": {"input": 2.50, "output": 15.00, "provider": "OpenAI"},
    "gpt-5.4-mini-2026-03-17": {"input": 0.75, "output": 4.50, "provider": "OpenAI"},
    "gpt-5.4-nano-2026-03-17": {"input": 0.20, "output": 1.25, "provider": "OpenAI"},
    "gpt-realtime-2025-08-28": {"input": 32.00, "output": 64.00, "provider": "OpenAI"},
    "gpt-realtime-mini-2025-10-06": {
        "input": 10.00,
        "output": 20.00,
        "provider": "OpenAI",
    },
    "gpt-realtime-mini-2025-12-15": {
        "input": 10.00,
        "output": 20.00,
        "provider": "OpenAI",
    },
    "gpt-realtime-1.5-2026-02-25": {
        "input": 32.00,
        "output": 64.00,
        "provider": "OpenAI",
    },
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60, "provider": "Groq"},
    "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30, "provider": "Groq"},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "provider": "Google"},
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00, "provider": "Google"},
    "gemini-flash-latest": {"input": 0.30, "output": 2.50, "provider": "Google"},
    "gemini-3.1-flash-image-preview": {
        "input": 0.50,
        "output": 3.00,
        "provider": "Google",
    },
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00, "provider": "Google"},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "provider": "Google"},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "provider": "Google"},
    "gemini-2.5-flash-image": {"input": 0.30, "output": 2.50, "provider": "Google"},
    "google/gemini-2.5-flash-lite": {
        "input": 0.10,
        "output": 0.40,
        "provider": "OpenRouter",
    },
    "google/gemini-3.1-pro-preview": {
        "input": 2.00,
        "output": 12.00,
        "provider": "OpenRouter",
    },
    "google/gemini-3-pro-preview": {
        "input": 2.00,
        "output": 12.00,
        "provider": "OpenRouter",
    },
    "google/gemini-3-flash-preview": {
        "input": 0.50,
        "output": 3.00,
        "provider": "OpenRouter",
    },
    "google/gemini-2.5-flash-image": {
        "input": 0.30,
        "output": 2.50,
        "provider": "OpenRouter",
    },
    "deepseek/deepseek-chat-v3.1": {
        "input": 0.28,
        "output": 0.42,
        "provider": "OpenRouter",
    },
    "deepseek/deepseek-r1-distill-qwen-7b": {
        "input": 0.55,
        "output": 2.19,
        "provider": "OpenRouter",
    },
    "moonshotai/kimi-k2-thinking": {
        "input": 0.50,
        "output": 1.50,
        "provider": "OpenRouter",
    },
}


def _get_token_value(obj: object, *keys: str) -> int:
    for key in keys:
        if isinstance(obj, dict):
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        if isinstance(value, int):
            return value
    return 0


def extract_usage_data(response: object) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not usage:
        return None

    input_tokens = _get_token_value(usage, "input_tokens", "prompt_tokens", "prompt_token_count")
    output_tokens = _get_token_value(
        usage, "output_tokens", "completion_tokens", "candidates_token_count"
    )
    total_tokens = _get_token_value(usage, "total_tokens") or (input_tokens + output_tokens)

    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def calculate_cost(
    model_id: str, input_tokens: int, output_tokens: int
) -> dict[str, float | str] | None:
    pricing = MODEL_PRICING.get(model_id)
    if not pricing:
        return None

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    total_cost = input_cost + output_cost
    return {
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "provider": pricing["provider"],
    }


def build_cost_metadata(model_id: str, response: object) -> dict[str, dict] | None:
    usage = extract_usage_data(response)
    if not usage:
        return None

    cost = calculate_cost(model_id, usage["input_tokens"], usage["output_tokens"])
    result = {"usage": usage}
    if cost:
        result["cost"] = cost
    return result
