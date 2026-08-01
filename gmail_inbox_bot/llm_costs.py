"""Adapt neutral gateway usage/cost metadata to the bot's legacy metric shape."""

from __future__ import annotations

from decimal import Decimal

from llm_gateway import LLMResult, lookup_model

_USD_PER_MTOK = Decimal(1_000_000)
_PROVIDER_LABELS = {"groq": "Groq", "openai": "OpenAI"}


def _split_attempt_cost(result: LLMResult) -> tuple[Decimal, Decimal] | None:
    input_cost = Decimal(0)
    output_cost = Decimal(0)
    priced = False
    for attempt in result.execution.attempts:
        model = lookup_model(attempt.model)
        if model is None:
            continue
        if attempt.usage.input_tokens is not None:
            input_cost += (
                Decimal(attempt.usage.input_tokens) * model.input_usd_per_mtok / _USD_PER_MTOK
            )
            priced = True
        if attempt.usage.output_tokens is not None:
            output_cost += (
                Decimal(attempt.usage.output_tokens) * model.output_usd_per_mtok / _USD_PER_MTOK
            )
            priced = True
    return (input_cost, output_cost) if priced else None


def build_cost_metadata(result: LLMResult) -> dict[str, dict] | None:
    usage = result.usage
    if usage.input_tokens is None and usage.output_tokens is None:
        return None

    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    metadata: dict[str, dict] = {
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    }

    split = _split_attempt_cost(result)
    if split is None or result.cost.amount_usd is None:
        return metadata

    input_cost, output_cost = split
    metadata["cost"] = {
        "input_cost_usd": round(float(input_cost), 6),
        "output_cost_usd": round(float(output_cost), 6),
        "total_cost_usd": float(result.cost.amount_usd),
        "provider": _PROVIDER_LABELS.get(
            result.execution.provider, result.execution.provider.title()
        ),
    }
    return metadata
