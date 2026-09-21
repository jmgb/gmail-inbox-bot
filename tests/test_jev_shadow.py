"""Tests for jev_shadow — clasificación sombra con Jev (TypeSafe.ai)."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml
from typesafe_sdk import Choice, RetryPolicy

from gmail_inbox_bot import jev_shadow
from gmail_inbox_bot.email_format import format_email_for_classifier
from gmail_inbox_bot.jev_shadow import (
    JEV_ERROR_MAX_CHARS,
    JEV_MAX_BODY_CHARS,
    JevShadow,
    build_jev_shadow,
    build_state,
    load_criteria,
)
from gmail_inbox_bot.metrics import record_email

CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
CONFIG_DIR = Path("config")


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

    def test_matches_llm_classifier_format(self):
        kwargs = dict(
            subject="Hola",
            body_text="Cuerpo corto",
            sender_name="Juan",
            sender_address="juan@x.com",
            has_attachments=False,
        )
        assert build_state(**kwargs) == format_email_for_classifier(**kwargs)


class TestBuildJevShadow:
    def test_returns_none_without_key(self):
        assert build_jev_shadow({}, CRITERIA_PATH) is None
        assert build_jev_shadow({"JEV_API_KEY": ""}, CRITERIA_PATH) is None

    def test_builds_client_with_key(self, monkeypatch):
        captured = {}

        def fake_client(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="jev-client")

        monkeypatch.setattr(jev_shadow, "TypeSafeClient", fake_client)
        shadow = build_jev_shadow({"JEV_API_KEY": "apikey_test"}, CRITERIA_PATH)
        assert isinstance(shadow, JevShadow)
        assert captured["api_key"] == "apikey_test"
        assert captured["timeout"] == jev_shadow.JEV_TIMEOUT_SECONDS
        assert isinstance(captured["retry"], RetryPolicy)
        assert captured["retry"].max_retries == 1
        expected = set(yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8")))
        assert set(shadow.question.criteria) == expected
        assert len(expected) == 8


class TestClassify:
    def _shadow(self, client):
        question = Choice(instructions="q", criteria={"spam": "x", "otros": "y"})
        return JevShadow(client=client, question=question)

    def test_maps_response_to_dict(self):
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
        assert questions["categoria"] is shadow.question

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

    def test_result_keys_are_record_email_kwargs(self):
        # bot.py hace ``record_email(**jev_result)``: cualquier clave desconocida
        # sería un TypeError en producción, en ambas ramas de ``classify``.
        accepted = set(inspect.signature(record_email).parameters)
        kwargs = dict(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )

        ok_client = MagicMock()
        ok_client.system_one.return_value = _fake_response()
        assert set(self._shadow(ok_client).classify(**kwargs)) <= accepted

        ko_client = MagicMock()
        ko_client.system_one.side_effect = RuntimeError("boom")
        assert set(self._shadow(ko_client).classify(**kwargs)) <= accepted
