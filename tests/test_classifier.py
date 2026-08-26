"""Contract tests for the synchronous classifier facade over the LLM gateway."""

from __future__ import annotations

from collections.abc import Iterable

from llm_gateway import (
    LLMGateway,
    LLMRequest,
    ProviderRegistry,
    ProviderResponse,
    RateLimitedError,
    ResponseFormat,
    TokenUsage,
)

from gmail_inbox_bot.classifier import (
    DEFAULT_MODEL,
    GPT_5_LUNA,
    GPT_OSS_120B,
    classify_email,
    generate_response,
)
from gmail_inbox_bot.llm_gateway_client import SynchronousLLMGateway


def _response(
    output: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        output_text=output,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        finish_reason="stop",
        model_used=None,
    )


class _ScriptedAdapter:
    """Provider double at the gateway port; no SDK or network is involved."""

    def __init__(self, name: str, outcomes: Iterable[ProviderResponse | Exception]) -> None:
        self.name = name
        self._outcomes = list(outcomes)
        self.calls: list[str] = []
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest, *, model: str) -> ProviderResponse:
        self.calls.append(model)
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _SynchronousGatewayDouble:
    """Test-side sync boundary with the same public port used by the bot."""

    def __init__(
        self,
        *,
        groq: Iterable[ProviderResponse | Exception] | None = None,
        openai: Iterable[ProviderResponse | Exception] | None = None,
    ) -> None:
        self.registry = ProviderRegistry()
        self.groq = _ScriptedAdapter("groq", groq) if groq is not None else None
        self.openai = _ScriptedAdapter("openai", openai) if openai is not None else None
        if self.groq is not None:
            self.registry.register(self.groq, model_prefixes=("openai/gpt-oss-",))
        if self.openai is not None:
            self.registry.register(self.openai, model_prefixes=("gpt-",))
        self.client = SynchronousLLMGateway(LLMGateway(registry=self.registry), self.registry)

    @property
    def provider_names(self) -> tuple[str, ...]:
        return self.client.provider_names

    def supports_model(self, model: str) -> bool:
        return self.client.supports_model(model)

    def generate(self, request: LLMRequest):
        return self.client.generate(request)


def _classification_client(
    payload: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> _SynchronousGatewayDouble:
    return _SynchronousGatewayDouble(
        groq=(
            _response(
                payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        ),
        openai=(),
    )


class TestClassifyEmail:
    def test_valid_response_preserves_public_shape(self):
        client = _classification_client(
            '{"idioma":"español","categoria":"coste_programa",'
            '"razon_clasificacion":"Pregunta si es gratuito",'
            '"ultimo_email":"Hola, ¿el programa es gratuito?"}'
        )

        result = classify_email(
            client,
            "system prompt",
            "Coste",
            "¿Es gratuito?",
            "Juan",
            "juan@test.com",
            False,
        )

        assert result["categoria"] == "coste_programa"
        assert result["idioma"] == "español"
        assert result["razon_clasificacion"] == "Pregunta si es gratuito"
        assert result["model_used"] == DEFAULT_MODEL

    def test_request_preserves_model_prompts_and_json_mode(self):
        client = _classification_client(
            '{"idioma":"español","categoria":"spam","razon_clasificacion":""}'
        )

        classify_email(
            client,
            "Mi system prompt",
            "Mi asunto especial",
            "Contenido del cuerpo",
            "María López",
            "maria@empresa.com",
            True,
            model=DEFAULT_MODEL,
        )

        request = client.groq.requests[0]
        assert request.model == DEFAULT_MODEL
        assert request.system_prompt == "Mi system prompt"
        assert request.response_format is ResponseFormat.JSON_OBJECT
        assert len(request.messages) == 1
        user_text = request.messages[0].content
        assert request.messages[0].role == "user"
        assert "Mi asunto especial" in user_text
        assert "Contenido del cuerpo" in user_text
        assert "María López" in user_text
        assert "maria@empresa.com" in user_text
        assert "True" in user_text

    def test_invalid_json_returns_none(self):
        client = _classification_client("not valid json {{{")

        result = classify_email(
            client,
            "system prompt",
            "Test",
            "body",
            "Juan",
            "juan@test.com",
            False,
        )

        assert result is None

    def test_gateway_failure_returns_none(self):
        client = _SynchronousGatewayDouble(groq=(RateLimitedError("API timeout"),))

        result = classify_email(
            client,
            "system prompt",
            "Test",
            "body",
            "Juan",
            "juan@test.com",
            False,
        )

        assert result is None

    def test_placeholder_reason_string_is_sanitized(self):
        client = _classification_client('{"categoria":"finanzas","razon_clasificacion":"string"}')

        result = classify_email(
            client,
            "system prompt",
            "Payment Reminder",
            "body",
            "Hetzner",
            "billing@hetzner.com",
            True,
        )

        assert result["categoria"] == "finanzas"
        assert result["razon_clasificacion"] == ""

    def test_placeholder_reason_field_name_is_sanitized(self):
        client = _classification_client(
            '{"categoria":"personal","razon_clasificacion":"razon_clasificacion"}'
        )

        result = classify_email(
            client,
            "system prompt",
            "Annual Report 2025: Signature Required",
            "body",
            "Companio",
            "noreply@companio.co",
            False,
        )

        assert result["categoria"] == "personal"
        assert result["razon_clasificacion"] == ""

    def test_reason_prefix_is_removed(self):
        client = _classification_client(
            '{"categoria":"finanzas","razon_clasificacion":'
            '"razon_clasificacion: Trata sobre un recordatorio de pago"}'
        )

        result = classify_email(
            client,
            "system prompt",
            "Payment Reminder",
            "body",
            "Katia",
            "katia@audifono.es",
            False,
        )

        assert result["categoria"] == "finanzas"
        assert result["razon_clasificacion"] == "Trata sobre un recordatorio de pago"

    def test_usage_and_cost_preserve_metrics_shape(self):
        client = _classification_client(
            '{"idioma":"español","categoria":"otros",'
            '"razon_clasificacion":"Clasificacion general","ultimo_email":"Texto"}',
            input_tokens=1200,
            output_tokens=300,
        )

        result = classify_email(
            client,
            "system prompt",
            "Test",
            "body",
            "Juan",
            "juan@test.com",
            False,
            model=DEFAULT_MODEL,
        )

        assert result["usage"] == {
            "input_tokens": 1200,
            "output_tokens": 300,
            "total_tokens": 1500,
        }
        assert result["cost"] == {
            "input_cost_usd": 0.00018,
            "output_cost_usd": 0.00018,
            "total_cost_usd": 0.00036,
            "provider": "Groq",
        }


class TestProviderRoutingAndFallback:
    def test_gpt_oss_model_is_routed_to_groq(self):
        client = _SynchronousGatewayDouble(
            groq=(_response('{"categoria":"otros","razon_clasificacion":""}'),),
            openai=(),
        )

        result = classify_email(
            client,
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert client.groq.calls == [GPT_OSS_120B]
        assert client.openai.calls == []
        assert result["model_used"] == GPT_OSS_120B

    def test_non_gpt_oss_model_is_routed_to_openai(self):
        client = _SynchronousGatewayDouble(
            groq=(),
            openai=(_response('{"categoria":"otros","razon_clasificacion":""}'),),
        )

        classify_email(
            client,
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_5_LUNA,
        )

        assert client.openai.calls == [GPT_5_LUNA]
        assert client.groq.calls == []
        assert client.openai.requests[0].reasoning_effort == "max"

    def test_groq_failure_falls_back_to_openai(self):
        client = _SynchronousGatewayDouble(
            groq=(RateLimitedError("Groq 429 quota"),),
            openai=(_response('{"categoria":"otros","razon_clasificacion":""}'),),
        )

        result = classify_email(
            client,
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert client.groq.calls == [GPT_OSS_120B]
        assert client.openai.calls == [GPT_5_LUNA]
        assert client.groq.requests[0].reasoning_effort is None
        assert client.openai.requests[0].reasoning_effort is None
        assert result["model_used"] == GPT_5_LUNA

    def test_groq_failure_falls_back_and_logs_the_reason(self, caplog):
        client = _SynchronousGatewayDouble(
            groq=(RateLimitedError("Groq 429 quota"),),
            openai=(_response('{"categoria":"otros","razon_clasificacion":""}'),),
        )

        with caplog.at_level("INFO", logger="gmail_inbox_bot.classifier"):
            classify_email(
                client,
                "system prompt",
                "s",
                "b",
                "n",
                "e@e.com",
                False,
                model=GPT_OSS_120B,
            )

        info_records = [record.message for record in caplog.records]
        assert any("Fallback usado en classify_email" in message for message in info_records)

    def test_both_providers_fail_returns_none(self):
        client = _SynchronousGatewayDouble(
            groq=(RateLimitedError("Groq down"),),
            openai=(RateLimitedError("OpenAI down"),),
        )

        result = classify_email(
            client,
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert client.groq.calls == [GPT_OSS_120B]
        assert client.openai.calls == [GPT_5_LUNA]
        assert result is None

    def test_both_providers_fail_logs_last_error(self, caplog):
        client = _SynchronousGatewayDouble(
            groq=(RateLimitedError("Groq down"),),
            openai=(RateLimitedError("OpenAI down"),),
        )

        with caplog.at_level("WARNING", logger="gmail_inbox_bot.classifier"):
            result = classify_email(
                client,
                "system prompt",
                "s",
                "b",
                "n",
                "e@e.com",
                False,
                model=GPT_OSS_120B,
            )

        assert result is None
        warning_records = [record.message for record in caplog.records]
        assert any("last_error=RateLimitedError" in message for message in warning_records)

    def test_missing_groq_client_starts_with_openai_fallback(self):
        client = _SynchronousGatewayDouble(
            groq=None,
            openai=(_response('{"categoria":"otros","razon_clasificacion":""}'),),
        )

        result = classify_email(
            client,
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert client.openai.calls == [GPT_5_LUNA]
        assert result["model_used"] == GPT_5_LUNA


class TestGenerateResponse:
    def test_text_result_preserves_public_shape_and_metrics(self):
        client = _SynchronousGatewayDouble(
            groq=(_response("Respuesta generada", input_tokens=1000, output_tokens=250),),
            openai=(),
        )

        result = generate_response(
            client,
            "system prompt",
            "body",
            "Juan",
            model=DEFAULT_MODEL,
        )

        assert result == {
            "text": "Respuesta generada",
            "model_used": DEFAULT_MODEL,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 250,
                "total_tokens": 1250,
            },
            "cost": {
                "input_cost_usd": 0.00015,
                "output_cost_usd": 0.00015,
                "total_cost_usd": 0.0003,
                "provider": "Groq",
            },
        }
        request = client.groq.requests[0]
        assert request.system_prompt == "system prompt"
        assert request.response_format is ResponseFormat.TEXT
        assert request.messages[0].content == "Remitente: Juan\n\nEmail:\nbody"

    def test_failure_returns_none(self):
        client = _SynchronousGatewayDouble(groq=(RateLimitedError("Groq down"),))

        result = generate_response(client, "system prompt", "body", "Juan")

        assert result is None
