"""Tests for classifier.py — OpenAI Responses API classification."""

import json
from unittest.mock import MagicMock

from gmail_inbox_bot.classifier import DEFAULT_MODEL, classify_email


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

        assert result == expected
        assert result["categoria"] == "coste_programa"
        assert result["idioma"] == "español"

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

        assert result == {"categoria": "finanzas", "razon_clasificacion": ""}

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

        assert result == {"categoria": "personal", "razon_clasificacion": ""}

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

        assert result == {
            "categoria": "finanzas",
            "razon_clasificacion": "Trata sobre un recordatorio de pago",
        }
