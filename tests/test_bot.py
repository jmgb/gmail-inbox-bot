"""Tests for bot.py — the polling orchestrator."""

from unittest.mock import MagicMock, patch

import pytest

from gmail_inbox_bot.bot import (
    _build_gmail_client,
    _enrich_forwarded,
    _process_email,
    process_mailbox,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def env():
    return {
        "GOOGLE_CLIENT_ID": "id",
        "GOOGLE_CLIENT_SECRET": "secret",
        "OPENAI_API_KEY": "sk-test",
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
