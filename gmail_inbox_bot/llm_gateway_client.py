"""Synchronous application boundary for the asynchronous neutral LLM gateway."""

from __future__ import annotations

import asyncio

from llm_gateway import LLMGateway, LLMRequest, LLMResult, ProviderRegistry, UnknownModelError


class SynchronousLLMGateway:
    """Keep the polling bot synchronous while reusing one async event loop."""

    def __init__(self, gateway: LLMGateway, registry: ProviderRegistry) -> None:
        self._gateway = gateway
        self._registry = registry
        self._runner = asyncio.Runner()

    @property
    def provider_names(self) -> tuple[str, ...]:
        return self._registry.provider_names

    def supports_model(self, model: str) -> bool:
        try:
            self._registry.resolve(model)
        except UnknownModelError:
            return False
        return True

    def generate(self, request: LLMRequest) -> LLMResult:
        return self._runner.run(self._gateway.generate(request))
