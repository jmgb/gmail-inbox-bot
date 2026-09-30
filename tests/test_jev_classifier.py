"""Tests for jev_classifier — clasificación con Jev (TypeSafe.ai), el clasificador principal."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
import yaml
from typesafe_sdk import Choice, TypeSafeClient

from gmail_inbox_bot import jev_classifier
from gmail_inbox_bot.jev_classifier import (
    JEV_ERROR_MAX_CHARS,
    JEV_MAX_BODY_CHARS,
    JevClassifier,
    build_jev_classifier,
    build_state,
    load_criteria,
)
from gmail_inbox_bot.metrics import record_email

CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
CONFIG_DIR = Path("config")
LLM_PROMPT_PATH = Path("gmail_inbox_bot/prompts/clasificador_inbox.txt")


class TestCriteriaFile:
    def test_every_category_has_what_not_for_examples(self):
        data = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data, "YAML vacío"
        for category, spec in data.items():
            assert set(spec) == {"what", "not_for", "examples"}, category
            assert isinstance(spec["what"], str) and spec["what"].strip(), category
            assert isinstance(spec["not_for"], str) and spec["not_for"].strip(), category
            assert isinstance(spec["examples"], list) and spec["examples"], category

    def test_categories_match_routing_of_every_mailbox(self):
        criteria = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        for yml in sorted(CONFIG_DIR.glob("*.yml")):
            config = yaml.safe_load(yml.read_text(encoding="utf-8"))
            assert set(criteria) == set(config["routing"]), yml.name

    def test_llm_fallback_prompt_lists_every_routing_category(self):
        # Jev decide, pero si falla clasifica el LLM: su prompt debe conocer las mismas
        # categorías o nunca elegirá una nueva y la mandará a otra carpeta.
        prompt = LLM_PROMPT_PATH.read_text(encoding="utf-8")
        headings = set(re.findall(r"^### (\w+)$", prompt, flags=re.MULTILINE))
        for yml in sorted(CONFIG_DIR.glob("*.yml")):
            config = yaml.safe_load(yml.read_text(encoding="utf-8"))
            assert headings == set(config["routing"]), yml.name

    def test_facturas_move_out_of_inbox_read(self):
        # Facturas y recibos de cobros ya hechos: comprobante para archivar, no piden nada.
        # Sin `is_read`, `_handle_move` las deja leídas (como `compras`).
        for yml in sorted(CONFIG_DIR.glob("*.yml")):
            config = yaml.safe_load(yml.read_text(encoding="utf-8"))
            rule = config["routing"]["facturas"]
            assert rule == {"action": "move", "folder": "Facturas"}, yml.name


def _fake_response(choice="spam", confidence=0.93, probabilities=None, model="jev-1.13.0"):
    answer = SimpleNamespace(
        choice=choice,
        confidence=confidence,
        probabilities=probabilities or {"spam": 0.93, "otros": 0.07},
    )
    return SimpleNamespace(model=model, choices={"categoria": answer})


class TestLoadCriteria:
    def test_returns_dict_keyed_by_category(self):
        criteria = load_criteria(CRITERIA_PATH)
        assert "spam" in criteria
        assert set(criteria["spam"]) == {"what", "not_for", "examples"}


class TestBuildState:
    def test_contains_all_fields(self):
        state = build_state(
            subject="Hola",
            body_text="Cuerpo del email",
            sender_name="Juan",
            sender_address="juan@x.com",
            has_attachments=True,
        )
        assert "Título del email: Hola" in state
        assert "¿Contiene archivo adjunto?: True" in state
        assert "Remitente: Juan <juan@x.com>" in state
        assert "Contenido del email:\nCuerpo del email" in state
        assert "JSON" not in state

    def test_truncates_long_body(self):
        state = build_state(
            subject="s",
            body_text="x" * (JEV_MAX_BODY_CHARS + 500),
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert state.count("x") == JEV_MAX_BODY_CHARS


class TestBuildJevClassifier:
    def test_returns_none_without_key(self):
        assert build_jev_classifier({}, CRITERIA_PATH) is None
        assert build_jev_classifier({"JEV_API_KEY": ""}, CRITERIA_PATH) is None

    def test_real_sdk_bounds_request_body_timeout_and_retries(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "jev-1.13.0")
        attempts = []

        def respond(request):
            attempts.append(request)
            raise httpx2.ReadTimeout("deliberate timeout", request=request)

        def client(**kwargs):
            return TypeSafeClient(**kwargs, transport=httpx2.MockTransport(respond))

        monkeypatch.setattr(jev_classifier, "TypeSafeClient", client)
        shadow = build_jev_classifier({"JEV_API_KEY": "apikey_test"}, CRITERIA_PATH)

        result = shadow.classify(
            subject="s",
            body_text="z" * 6500,
            sender_name="n",
            sender_address="a@example.test",
            has_attachments=False,
        )

        # Un solo intento y 4 s: la sombra está en el camino crítico del poll y no decide
        # nada, así que no puede bloquear el procesamiento del email más que eso.
        assert len(attempts) == 1
        for request in attempts:
            assert request.extensions["timeout"] == {
                "connect": 4.0,
                "read": 4.0,
                "write": 4.0,
                "pool": 4.0,
            }
            payload = json.loads(request.content)
            assert payload["model"] == "jev-latest"
            assert payload["state"].split("Contenido del email:\n", 1)[1] == "z" * 6000
        assert result["jev_error"].startswith("TypeSafeAPITimeoutError:")
        assert "jev_category" not in result


class TestClassify:
    def _shadow(self, client):
        question = Choice(instructions="q", criteria={"spam": "x", "otros": "y"})
        return JevClassifier(client=client, question=question)

    def test_maps_response_to_dict(self, monkeypatch):
        observed = []
        monkeypatch.setattr(jev_classifier, "observe_jev_model", observed.append)
        client = MagicMock()
        client.system_one.return_value = _fake_response()
        result = self._shadow(client).classify(
            subject="Oferta",
            body_text="Compra ya",
            sender_name="",
            sender_address="promo@x.com",
            has_attachments=False,
        )
        assert result["jev_category"] == "spam"
        assert result["jev_confidence"] == 0.93
        assert result["jev_probabilities"] == {"spam": 0.93, "otros": 0.07}
        assert result["jev_model"] == "jev-1.13.0"
        assert observed == ["jev-1.13.0"]
        assert isinstance(result["jev_latency_ms"], int)
        assert "jev_error" not in result

    def test_sends_state_and_choice_question(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response()
        shadow = self._shadow(client)
        shadow.classify(
            subject="Oferta",
            body_text="Compra ya",
            sender_name="",
            sender_address="promo@x.com",
            has_attachments=False,
        )
        state, questions = client.system_one.call_args.args
        assert "Título del email: Oferta" in state
        assert list(questions) == ["categoria"]
        assert questions["categoria"].criteria == {"spam": "x", "otros": "y"}

    def test_exception_becomes_jev_error(self):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("boom")
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert result["jev_error"] == "RuntimeError: boom"
        assert isinstance(result["jev_latency_ms"], int)
        assert "jev_category" not in result

    def test_missing_answer_becomes_jev_error(self):
        client = MagicMock()
        client.system_one.return_value = SimpleNamespace(model="jev", choices={})
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert result["jev_error"].startswith("KeyError")

    def test_error_message_is_truncated(self):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("x" * 500)
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert len(result["jev_error"]) == JEV_ERROR_MAX_CHARS

    def test_build_state_failure_becomes_jev_error(self):
        client = MagicMock()
        result = self._shadow(client).classify(
            subject="s",
            body_text=None,
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert result["jev_error"].startswith("TypeError")
        assert isinstance(result["jev_latency_ms"], int)
        client.system_one.assert_not_called()

    def test_zero_confidence_and_latency_survive_metrics_persistence(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://metrics.example.test")
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
        monkeypatch.setattr(jev_classifier.time, "perf_counter", lambda: 42.0)
        post = MagicMock()
        monkeypatch.setattr("httpx2.post", post)
        client = MagicMock()
        client.system_one.return_value = _fake_response(
            confidence=0.0, probabilities={"spam": 0.0, "otros": 1.0}
        )
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )

        record_email(mailbox="test", category="otros", msg_id="zero", **result)

        payload = post.call_args.kwargs["json"]
        assert payload["jev_confidence"] == 0.0
        assert payload["jev_latency_ms"] == 0
        assert payload["jev_probabilities"] == {"spam": 0.0, "otros": 1.0}
        assert "jev_error" not in payload


class TestRazonDeJev:
    """El motivo lo sintetiza el código: Jev no devuelve prosa como el LLM."""

    def test_incluye_la_segunda_opcion_cuando_es_relevante(self):
        razon = jev_classifier._jev_reason(
            {
                "jev_category": "newsletters",
                "jev_confidence": 0.62,
                "jev_probabilities": {"newsletters": 0.62, "spam": 0.31, "otros": 0.07},
            }
        )
        assert "0.62" in razon
        assert "spam" in razon and "0.31" in razon

    def test_omite_la_segunda_opcion_residual(self):
        razon = jev_classifier._jev_reason(
            {
                "jev_category": "compras",
                "jev_confidence": 1.0,
                "jev_probabilities": {"compras": 1.0, "notificaciones": 0.0},
            }
        )
        assert razon == "Clasificado por Jev con confianza 1.00"

    def test_aguanta_una_respuesta_sin_probabilidades(self):
        razon = jev_classifier._jev_reason({"jev_category": "otros", "jev_confidence": 0.5})
        assert razon == "Clasificado por Jev con confianza 0.50"


class TestClassificationFromJev:
    def test_traduce_al_contrato_del_pipeline(self):
        clasificacion = jev_classifier.classification_from_jev(
            {
                "jev_category": "finanzas",
                "jev_confidence": 0.88,
                "jev_probabilities": {"finanzas": 0.88, "spam": 0.12},
                "jev_model": "jev-1.13.0",
            }
        )
        assert clasificacion["categoria"] == "finanzas"
        assert clasificacion["model_used"] == "jev-1.13.0"
        # Sin usage ni cost: Jev no factura por tokens y un cero falsearía el coste LLM.
        assert "usage" not in clasificacion
        assert "cost" not in clasificacion
