"""Tests for classifier.py — OpenAI Responses API classification."""

import json
from unittest.mock import MagicMock

from gmail_inbox_bot.classifier import (
    DEFAULT_MODEL,
    GPT_5_MINI,
    GPT_OSS_120B,
    classify_email,
    generate_response,
)


def _mock_responses_response(content: str) -> MagicMock:
    """Build a mock OpenAI Responses API response."""
    resp = MagicMock()
    resp.output_text = content
    return resp


class TestClassifyEmail:
    def test_valid_response_parsed(self):
        expected = {
            "idioma": "español",
            "categoria": "coste_programa",
            "razon_clasificacion": "Pregunta si es gratuito",
            "ultimo_email": "Hola, ¿el programa es gratuito?",
        }
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(json.dumps(expected))

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
        assert result["razon_clasificacion"] == expected["razon_clasificacion"]
        assert result["model_used"] == DEFAULT_MODEL

    def test_invalid_json_returns_none(self):
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response("not valid json {{{")

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

    def test_openai_exception_returns_none(self):
        client = MagicMock()
        client.responses.create.side_effect = Exception("API timeout")

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

    def test_api_called_with_correct_params(self):
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
            '{"idioma":"español","categoria":"spam","razon_clasificacion":"","ultimo_email":""}'
        )

        classify_email(
            client,
            "Mi system prompt",
            "Asunto del email",
            "Cuerpo del email",
            "María López",
            "maria@empresa.com",
            True,
            model=DEFAULT_MODEL,
        )

        call_kwargs = client.responses.create.call_args[1]
        assert call_kwargs["model"] == DEFAULT_MODEL
        assert call_kwargs["instructions"] == "Mi system prompt"
        assert call_kwargs["text"] == {
            "format": {"type": "json_object"},
        }

    def test_user_prompt_contains_email_fields(self):
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
            '{"idioma":"español","categoria":"otros","razon_clasificacion":"","ultimo_email":""}'
        )

        classify_email(
            client,
            "system prompt",
            "Mi asunto especial",
            "Contenido del cuerpo",
            "Pedro Ruiz",
            "pedro@empresa.com",
            True,
        )

        call_kwargs = client.responses.create.call_args[1]
        input_messages = call_kwargs["input"]
        assert input_messages[0]["role"] == "user"
        # Extract text from input_text content block
        user_text = input_messages[0]["content"][0]["text"]
        assert "Mi asunto especial" in user_text
        assert "Contenido del cuerpo" in user_text
        assert "Pedro Ruiz" in user_text
        assert "pedro@empresa.com" in user_text
        assert "True" in user_text  # hasAttachments

    def test_system_prompt_passed_as_instructions(self):
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
            '{"idioma":"español","categoria":"spam","razon_clasificacion":"","ultimo_email":""}'
        )

        classify_email(
            client,
            "CUSTOM SYSTEM PROMPT HERE",
            "s",
            "b",
            "n",
            "e",
            False,
        )

        call_kwargs = client.responses.create.call_args[1]
        assert call_kwargs["instructions"] == "CUSTOM SYSTEM PROMPT HERE"

    def test_placeholder_reason_string_is_sanitized(self):
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
            '{"categoria":"finanzas","razon_clasificacion":"string"}'
        )

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
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
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
        client = MagicMock()
        client.responses.create.return_value = _mock_responses_response(
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

    def test_usage_and_cost_are_included_in_classification_result(self):
        expected = {
            "idioma": "español",
            "categoria": "otros",
            "razon_clasificacion": "Clasificacion general",
            "ultimo_email": "Texto",
        }
        response = _mock_responses_response(json.dumps(expected))
        response.usage = MagicMock(input_tokens=1200, output_tokens=300)
        client = MagicMock()
        client.responses.create.return_value = response

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
    def test_gpt_oss_model_is_routed_to_groq_client(self):
        response = _mock_responses_response('{"categoria":"otros","razon_clasificacion":""}')
        groq_client = MagicMock(name="groq")
        groq_client.responses.create.return_value = response
        openai_client = MagicMock(name="openai")

        result = classify_email(
            {"openai": openai_client, "groq": groq_client},
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        groq_client.responses.create.assert_called_once()
        openai_client.responses.create.assert_not_called()
        assert result["model_used"] == GPT_OSS_120B

    def test_non_gpt_oss_model_is_routed_to_openai_client(self):
        response = _mock_responses_response('{"categoria":"otros","razon_clasificacion":""}')
        openai_client = MagicMock(name="openai")
        openai_client.responses.create.return_value = response
        groq_client = MagicMock(name="groq")

        classify_email(
            {"openai": openai_client, "groq": groq_client},
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_5_MINI,
        )

        openai_client.responses.create.assert_called_once()
        groq_client.responses.create.assert_not_called()

    def test_groq_failure_falls_back_to_openai(self):
        ok_response = _mock_responses_response('{"categoria":"otros","razon_clasificacion":""}')
        groq_client = MagicMock(name="groq")
        groq_client.responses.create.side_effect = Exception("Groq 429 quota")
        openai_client = MagicMock(name="openai")
        openai_client.responses.create.return_value = ok_response

        result = classify_email(
            {"openai": openai_client, "groq": groq_client},
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert groq_client.responses.create.call_count == 1
        assert openai_client.responses.create.call_count == 1
        assert openai_client.responses.create.call_args.kwargs["model"] == GPT_5_MINI
        assert result["model_used"] == GPT_5_MINI

    def test_both_providers_fail_returns_none(self):
        groq_client = MagicMock(name="groq")
        groq_client.responses.create.side_effect = Exception("Groq down")
        openai_client = MagicMock(name="openai")
        openai_client.responses.create.side_effect = Exception("OpenAI down")

        result = classify_email(
            {"openai": openai_client, "groq": groq_client},
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert result is None

    def test_missing_groq_client_falls_back_to_openai(self):
        ok_response = _mock_responses_response('{"categoria":"otros","razon_clasificacion":""}')
        openai_client = MagicMock(name="openai")
        openai_client.responses.create.return_value = ok_response

        result = classify_email(
            {"openai": openai_client, "groq": None},
            "system prompt",
            "s",
            "b",
            "n",
            "e@e.com",
            False,
            model=GPT_OSS_120B,
        )

        assert openai_client.responses.create.call_args.kwargs["model"] == GPT_5_MINI
        assert result["model_used"] == GPT_5_MINI


class TestGenerateResponse:
    def test_usage_and_cost_are_returned_with_generated_text(self):
        response = _mock_responses_response("Respuesta generada")
        response.usage = MagicMock(input_tokens=1000, output_tokens=250)
        client = MagicMock()
        client.responses.create.return_value = response

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
