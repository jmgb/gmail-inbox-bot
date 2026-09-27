"""Tests for bot.py — the polling orchestrator."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from typesafe_sdk import Choice

from gmail_inbox_bot.bot import (
    _build_gmail_client,
    _build_llm_clients,
    _enrich_forwarded,
    _process_email,
    process_mailbox,
)
from gmail_inbox_bot.classifier import GPT_OSS_120B
from gmail_inbox_bot.jev_shadow import JevShadow
from gmail_inbox_bot.telegram_logger import TelegramHandler

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def env():
    return {
        "GOOGLE_CLIENT_ID": "id",
        "GOOGLE_CLIENT_SECRET": "secret",
        "OPENAI_API_KEY": "sk-test",
        "GROQ_API_KEY": "gsk-test",
        "LOG_LEVEL": "INFO",
        "ENVIRONMENT": "test",
    }


@pytest.fixture
def mock_gmail():
    g = MagicMock()
    g.draft_mode = False
    g.get_unread_emails.return_value = []
    return g


@pytest.fixture
def mailbox_config():
    return {
        "name": "TestMailbox",
        "email": "bot@example.com",
        "refresh_token_env": "GOOGLE_REFRESH_TOKEN_TEST",
        "classifier": {"prompt_file": "gmail_inbox_bot/prompts/clasificador_inbox.txt"},
        "routing": {"spam": {"action": "silent"}},
        "templates": {},
        "max_emails_per_poll": 50,
        "poll_interval_seconds": 600,
    }


@pytest.fixture
def config(mailbox_config):
    """Alias — most tests only need the config dict, not the env."""
    return mailbox_config


def _make_email(**overrides):
    base = {
        "id": "msg_001",
        "threadId": "thread_001",
        "subject": "Pregunta",
        "from": {"emailAddress": {"name": "Juan", "address": "juan@empresa.com"}},
        "sender": {"emailAddress": {"name": "Juan", "address": "juan@empresa.com"}},
        "body": {"content": "<p>Hola</p>"},
        "hasAttachments": False,
        "labels": [],
        "categories": [],
        "receivedDateTime": "2026-03-12T10:00:00Z",
        "internetMessageId": "<abc@mail.gmail.com>",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# _build_gmail_client
# ------------------------------------------------------------------


class TestBuildGmailClient:
    def test_builds_with_env_and_config(self, env, mailbox_config):
        with patch.dict("os.environ", {"GOOGLE_REFRESH_TOKEN_TEST": "test-token"}):
            client = _build_gmail_client(env, mailbox_config)
            assert client.client_id == "id"
            assert client.draft_mode is False

    def test_send_as_none_when_empty(self, env, mailbox_config):
        with patch.dict("os.environ", {"GOOGLE_REFRESH_TOKEN_TEST": "test-token"}):
            client = _build_gmail_client(env, mailbox_config)
            assert client.send_as is None

    def test_send_as_set(self, env, mailbox_config):
        mailbox_config["send_as"] = "alias@dom.com"
        with patch.dict("os.environ", {"GOOGLE_REFRESH_TOKEN_TEST": "test-token"}):
            client = _build_gmail_client(env, mailbox_config)
            assert client.send_as == "alias@dom.com"

    def test_draft_mode(self, env, mailbox_config):
        with patch.dict("os.environ", {"GOOGLE_REFRESH_TOKEN_TEST": "test-token"}):
            client = _build_gmail_client(env, mailbox_config, draft_mode=True)
            assert client.draft_mode is True

    def test_missing_token_raises(self, env, mailbox_config):
        with patch.dict("os.environ", {}, clear=False):
            mailbox_config["refresh_token_env"] = "NONEXISTENT_VAR"
            with pytest.raises(RuntimeError, match="not found or empty"):
                _build_gmail_client(env, mailbox_config)


class TestBuildLlmClients:
    def test_builds_official_adapters_with_explicit_app_credentials(self, monkeypatch, env):
        captured: dict[str, str] = {}

        def fake_openai_factory(*, api_key: str):
            captured["openai"] = api_key
            return MagicMock(name="async-openai")

        def fake_groq_factory(*, api_key: str):
            captured["groq"] = api_key
            return MagicMock(name="async-groq")

        monkeypatch.setattr(
            "gmail_inbox_bot.bot.create_openai_client", fake_openai_factory, raising=False
        )
        monkeypatch.setattr(
            "gmail_inbox_bot.bot.create_groq_client", fake_groq_factory, raising=False
        )

        client = _build_llm_clients(env)

        assert captured == {"openai": "sk-test", "groq": "gsk-test"}
        assert client.provider_names == ("groq", "openai")
        assert callable(client.generate)

    def test_no_credentials_returns_no_client(self, env):
        env["OPENAI_API_KEY"] = ""
        env["GROQ_API_KEY"] = ""

        assert _build_llm_clients(env) is None


# ------------------------------------------------------------------
# _enrich_forwarded
# ------------------------------------------------------------------


class TestEnrichForwarded:
    def test_not_forwarded(self):
        msg = _make_email()
        config = {"forwarded_from": []}
        _enrich_forwarded(msg, config)
        assert "_original_sender" not in msg
        assert "_forward_extraction_failed" not in msg

    def test_forwarded_with_extraction(self):
        body = "<p><b>De:</b> María López &lt;maria@org.com&gt;</p>"
        msg = _make_email(
            body={"content": body},
            **{"from": {"emailAddress": {"name": "Forwarder", "address": "fwd@relay.com"}}},
        )
        config = {"forwarded_from": ["relay.com"]}
        _enrich_forwarded(msg, config)
        assert msg["_original_sender"]["address"] == "maria@org.com"

    def test_forwarded_extraction_failed(self):
        msg = _make_email(
            body={"content": "<p>No sender info here</p>"},
            **{"from": {"emailAddress": {"name": "Forwarder", "address": "fwd@relay.com"}}},
        )
        config = {"forwarded_from": ["relay.com"]}
        _enrich_forwarded(msg, config)
        assert msg.get("_forward_extraction_failed") is True


# ------------------------------------------------------------------
# _process_email
# ------------------------------------------------------------------


class TestProcessEmail:
    def test_skip_already_processed(self, mock_gmail, config):
        msg = _make_email(labels=["RESPONDIDO IA"], categories=["RESPONDIDO IA"])
        result = _process_email(mock_gmail, None, config, msg)
        assert "skipped" in result

    def test_pre_filter_match(self, mock_gmail, config):
        config["pre_filters"] = [
            {"name": "spam-filter", "match": {"sender_contains": "spam.com"}, "action": "silent"}
        ]
        msg = _make_email(
            **{"from": {"emailAddress": {"name": "Spam", "address": "x@spam.com"}}},
        )
        result = _process_email(mock_gmail, None, config, msg)
        assert "pre-filter" in result
        mock_gmail.update_email.assert_called_once()

    def test_no_openai_tags_error(self, mock_gmail, config):
        msg = _make_email()
        result = _process_email(mock_gmail, None, config, msg)
        assert "ERROR IA" in result
        mock_gmail.update_email.assert_called_once()
        call_kwargs = mock_gmail.update_email.call_args
        assert call_kwargs.kwargs["is_read"] is False
        assert call_kwargs.kwargs["add_categories"] == ["ERROR IA"]

    @patch("gmail_inbox_bot.bot.classify_email")
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_classification_failure_tags_error(self, mock_load, mock_classify, mock_gmail, config):
        mock_classify.return_value = None
        openai_client = MagicMock()
        msg = _make_email()
        result = _process_email(mock_gmail, openai_client, config, msg)
        assert "ERROR IA" in result

    @patch("gmail_inbox_bot.bot.execute", return_value="replied (coste_programa, esp)")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={
            "categoria": "coste_programa",
            "idioma": "español",
            "razon_clasificacion": "",
        },
    )
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_successful_classification_and_execute(
        self, mock_load, mock_classify, mock_execute, mock_gmail, config
    ):
        openai_client = MagicMock()
        msg = _make_email()
        result = _process_email(mock_gmail, openai_client, config, msg)
        assert "replied" in result
        mock_execute.assert_called_once()

    @patch("gmail_inbox_bot.bot.classify_email")
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_classifier_receives_client_dict(self, _mock_load, mock_classify, mock_gmail, config):
        config["classifier"]["model"] = GPT_OSS_120B
        llm_clients = {"openai": MagicMock(name="openai"), "groq": MagicMock(name="groq")}
        mock_classify.return_value = {
            "categoria": "otros",
            "razon_clasificacion": "",
        }

        _process_email(mock_gmail, llm_clients, config, _make_email())

        # Routing ahora vive en classifier._select_client — bot pasa el dict tal cual.
        assert mock_classify.call_args.args[0] is llm_clients

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch("gmail_inbox_bot.bot.classify_email")
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_jev_decide_y_no_se_llama_al_llm(
        self, _mock_load, mock_classify, mock_execute, mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_category": "spam",
            "jev_confidence": 0.9,
            "jev_probabilities": {"spam": 0.9, "otros": 0.1},
            "jev_latency_ms": 500,
            "jev_model": "jev-1.13.0",
        }
        msg = _make_email(subject="Oferta", body={"content": "<p>Compra ya</p>"})

        _process_email(mock_gmail, MagicMock(), config, msg, jev=jev)

        jev.classify.assert_called_once()
        call = jev.classify.call_args.kwargs
        assert call["subject"] == "Oferta"
        assert call["body_text"] == "Compra ya"
        assert call["sender_address"] == "juan@empresa.com"
        # Jev decide: la cadena LLM no se toca y no hay tokens ni coste que registrar.
        mock_classify.assert_not_called()
        assert mock_execute.call_args.args[3]["categoria"] == "spam"
        recorded = mock_record.call_args.kwargs
        assert recorded["category"] == "spam"
        assert recorded["model"] == "jev-1.13.0"
        assert recorded["total_cost_usd"] is None
        assert recorded["total_tokens"] is None
        assert recorded["jev_category"] == "spam"
        assert recorded["jev_confidence"] == 0.9
        assert recorded["jev_latency_ms"] == 500

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch("gmail_inbox_bot.bot.classify_email")
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_la_razon_registrada_lleva_la_confianza_de_jev(
        self, _mock_load, _mock_classify, _mock_execute, mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_category": "newsletters",
            "jev_confidence": 0.89,
            "jev_probabilities": {"newsletters": 0.89, "notificaciones": 0.07, "spam": 0.04},
            "jev_latency_ms": 300,
            "jev_model": "jev-1.13.0",
        }

        _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=jev)

        razon = mock_record.call_args.kwargs["classification_reason"]
        assert "0.89" in razon
        assert "notificaciones" in razon

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={"categoria": "personal", "razon_clasificacion": "pregunta directa"},
    )
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_error_de_jev_cae_a_la_cadena_llm(
        self, _mock_load, mock_classify, mock_execute, mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_error": "TypeSafeAPITimeoutError: boom",
            "jev_latency_ms": 4000,
        }

        _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=jev)

        mock_classify.assert_called_once()
        assert mock_execute.call_args.args[3]["categoria"] == "personal"
        recorded = mock_record.call_args.kwargs
        assert recorded["category"] == "personal"
        assert recorded["jev_error"].startswith("TypeSafeAPITimeoutError")
        assert "jev_category" not in recorded

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.classify_email", return_value=None)
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_error_de_jev_y_del_llm_etiqueta_error_ia(
        self, _mock_load, mock_classify, mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_error": "TypeSafeAPITimeoutError: boom",
            "jev_latency_ms": 4000,
        }

        result = _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=jev)

        mock_classify.assert_called_once()
        assert "ERROR IA" in result
        mock_gmail.update_email.assert_called_once_with(
            config["email"], "msg_001", is_read=False, add_categories=["ERROR IA"]
        )
        assert mock_record.call_args.kwargs["category"] == "error_clasificacion"

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={"categoria": "spam", "razon_clasificacion": ""},
    )
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_sin_jev_configurado_decide_el_llm(
        self, _mock_load, _mock_classify, _mock_execute, mock_record, mock_gmail, config
    ):
        """Vaciar JEV_API_KEY es el camino de vuelta: build_jev_* devuelve None."""
        _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=None)
        _mock_classify.assert_called_once()
        recorded = mock_record.call_args.kwargs
        assert recorded["category"] == "spam"
        assert not any(key.startswith("jev_") for key in recorded)

    @patch("gmail_inbox_bot.bot.record_email")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={"categoria": "spam", "razon_clasificacion": ""},
    )
    def test_jev_failure_preserves_action_without_telegram(
        self, _mock_classify, mock_record, mock_gmail, config, monkeypatch
    ):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("x" * 500)
        jev = JevShadow(
            client=client, question=Choice(instructions="q", criteria={"spam": "x", "otros": "y"})
        )
        telegram = MagicMock()
        monkeypatch.setattr("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram", telegram)
        logger = logging.getLogger("gmail_inbox_bot")
        handler = TelegramHandler()
        logger.addHandler(handler)
        try:
            _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=jev)
        finally:
            logger.removeHandler(handler)
            handler.close()

        mock_gmail.update_email.assert_called_once_with(config["email"], "msg_001", is_read=True)
        telegram.assert_not_called()
        recorded = mock_record.call_args.kwargs
        assert recorded["category"] == "spam"
        assert recorded["jev_error"] == "RuntimeError: " + "x" * 186
        assert "jev_category" not in recorded


# ------------------------------------------------------------------
# process_mailbox
# ------------------------------------------------------------------


class TestProcessMailbox:
    def test_no_emails(self, mock_gmail, config):
        results = process_mailbox(mock_gmail, None, config)
        assert results == []

    def test_fetch_error(self, mock_gmail, config):
        mock_gmail.get_unread_emails.side_effect = Exception("network error")
        results = process_mailbox(mock_gmail, None, config)
        assert len(results) == 1
        assert "error" in results[0]

    def test_processes_each_email(self, mock_gmail, config):
        msg1 = _make_email(id="m1", labels=["RESPONDIDO IA"], categories=["RESPONDIDO IA"])
        msg2 = _make_email(id="m2", labels=["RESPONDIDO IA"], categories=["RESPONDIDO IA"])
        mock_gmail.get_unread_emails.return_value = [msg1, msg2]
        results = process_mailbox(mock_gmail, None, config)
        assert len(results) == 2
        assert all("skipped" in r for r in results)

    def test_unhandled_error_tags_error_ia(self, mock_gmail, config):
        msg = _make_email(id="crash")
        mock_gmail.get_unread_emails.return_value = [msg]

        # Make _process_email crash by injecting a bad pre_filters value
        config["pre_filters"] = "not-a-list"
        results = process_mailbox(mock_gmail, None, config)
        assert len(results) == 1
        assert "error" in results[0]

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.classify_email")
    def test_la_categoria_de_jev_llega_hasta_la_accion_en_el_buzon(
        self, mock_classify, mock_record, mock_gmail, config
    ):
        """Extremo a extremo sin mockear execute: lo que Jev decide es lo que se hace.

        Antes del 2026-09-27 este test comprobaba lo contrario — que un desacuerdo de la
        sombra NO cambiaba la acción. Ahora Jev es el clasificador principal.
        """
        mock_gmail.get_unread_emails.return_value = [_make_email()]
        config["routing"]["personal"] = {"action": "tag", "tag": "REVISAR IA"}
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_category": "personal",
            "jev_confidence": 0.9,
            "jev_latency_ms": 12,
        }

        process_mailbox(mock_gmail, MagicMock(), config, jev=jev)

        mock_classify.assert_not_called()
        update = mock_gmail.update_email.call_args
        assert update.args[:2] == (config["email"], "msg_001")
        assert update.kwargs["add_categories"] == ["REVISAR IA"]
        recorded = mock_record.call_args.kwargs
        assert recorded["category"] == "personal"
        assert recorded["jev_category"] == "personal"
