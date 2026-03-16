"""Tests for GmailClient — auth, normalisation, labels, and write operations."""

import base64
from unittest.mock import MagicMock

import pytest

from gmail_inbox_bot.gmail_client import (
    GmailClient,
    _decode_body,
    _get_header,
    _has_attachments,
    _normalise_message,
    _parse_address,
)
from gmail_inbox_bot.mail_client import MailClient

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

RAW_GMAIL_MESSAGE = {
    "id": "18e1a2b3c4d5e6f7",
    "threadId": "18e1a2b3c4d5e000",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "Hola, quiero información...",
    "internalDate": "1710244800000",  # 2024-03-12T12:00:00Z
    "payload": {
        "mimeType": "multipart/alternative",
        "headers": [
            {"name": "Subject", "value": "Pregunta sobre el programa"},
            {"name": "From", "value": "Juan García <juan@empresa.com>"},
            {"name": "To", "value": "bot@midominio.com"},
            {"name": "Date", "value": "Tue, 12 Mar 2024 13:00:00 +0100"},
            {"name": "Message-ID", "value": "<abc123@mail.gmail.com>"},
            {"name": "References", "value": "<prev@mail.gmail.com>"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(
                        "Hola, quiero información sobre el programa.".encode()
                    ).decode()
                },
            },
            {
                "mimeType": "text/html",
                "body": {
                    "data": base64.urlsafe_b64encode(
                        "<p>Hola, quiero información sobre el programa.</p>".encode()
                    ).decode()
                },
            },
        ],
    },
    "sizeEstimate": 2048,
}


def _make_client(**overrides) -> GmailClient:
    defaults = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "refresh_token": "test-refresh-token",
    }
    defaults.update(overrides)
    return GmailClient(**defaults)


@pytest.fixture
def client():
    return _make_client()


@pytest.fixture
def mock_http(client):
    """Replace the internal httpx client with a mock."""
    mock = MagicMock()
    client._http = mock
    client._access_token = "fake-token"
    return mock


# ------------------------------------------------------------------
# Protocol conformance
# ------------------------------------------------------------------


class TestProtocol:
    def test_gmail_client_satisfies_mail_client(self):
        assert isinstance(_make_client(), MailClient)

    def test_draft_mode_default_false(self, client):
        assert client.draft_mode is False

    def test_draft_mode_enabled(self):
        c = _make_client(draft_mode=True)
        assert c.draft_mode is True


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------


class TestNormaliseMessage:
    def test_basic_fields(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["id"] == "18e1a2b3c4d5e6f7"
        assert msg["threadId"] == "18e1a2b3c4d5e000"
        assert msg["subject"] == "Pregunta sobre el programa"

    def test_from_parsed(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["from"]["emailAddress"]["name"] == "Juan García"
        assert msg["from"]["emailAddress"]["address"] == "juan@empresa.com"

    def test_sender_mirrors_from(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["sender"] == msg["from"]

    def test_body_prefers_html(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert "<p>" in msg["body"]["content"]

    def test_labels_and_categories_both_populated(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert "INBOX" in msg["labels"]
        assert "UNREAD" in msg["labels"]
        assert msg["categories"] == msg["labels"]

    def test_received_datetime_iso(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["receivedDateTime"].startswith("2024-03-12")

    def test_internet_message_id(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["internetMessageId"] == "<abc123@mail.gmail.com>"

    def test_has_attachments_false(self):
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert msg["hasAttachments"] is False

    def test_has_attachments_true(self):
        raw = {
            "id": "x",
            "labelIds": [],
            "internalDate": "0",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": ""}},
                    {
                        "mimeType": "application/pdf",
                        "filename": "factura.pdf",
                        "body": {"attachmentId": "att1", "size": 12345},
                    },
                ],
            },
        }
        msg = _normalise_message(raw)
        assert msg["hasAttachments"] is True


class TestParseAddress:
    def test_name_and_email(self):
        result = _parse_address("Juan García <juan@empresa.com>")
        assert result == {"emailAddress": {"name": "Juan García", "address": "juan@empresa.com"}}

    def test_email_only(self):
        result = _parse_address("juan@empresa.com")
        assert result == {"emailAddress": {"name": "", "address": "juan@empresa.com"}}

    def test_quoted_name(self):
        result = _parse_address('"García, Juan" <juan@empresa.com>')
        assert result["emailAddress"]["name"] == "García, Juan"
        assert result["emailAddress"]["address"] == "juan@empresa.com"


class TestGetHeader:
    def test_found(self):
        headers = [{"name": "Subject", "value": "Hola"}]
        assert _get_header(headers, "Subject") == "Hola"

    def test_case_insensitive(self):
        headers = [{"name": "message-id", "value": "<x>"}]
        assert _get_header(headers, "Message-ID") == "<x>"

    def test_missing(self):
        assert _get_header([], "Subject") == ""


class TestDecodeBody:
    def test_plain_only(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hello").decode()},
        }
        assert _decode_body(payload) == "Hello"

    def test_html_preferred(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"plain").decode()},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<b>html</b>").decode()},
                },
            ],
        }
        assert _decode_body(payload) == "<b>html</b>"

    def test_nested_multipart(self):
        """multipart/mixed wrapping multipart/alternative."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"nested plain").decode()},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": base64.urlsafe_b64encode(b"<p>nested</p>").decode()},
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "file.pdf",
                    "body": {"attachmentId": "att1", "size": 100},
                },
            ],
        }
        assert _decode_body(payload) == "<p>nested</p>"

    def test_empty_payload(self):
        assert _decode_body({"mimeType": "text/plain", "body": {}}) == ""


class TestHasAttachments:
    def test_no_parts(self):
        assert _has_attachments({}) is False

    def test_with_attachment(self):
        payload = {
            "parts": [
                {"mimeType": "text/html"},
                {"mimeType": "application/pdf", "filename": "doc.pdf"},
            ]
        }
        assert _has_attachments(payload) is True

    def test_no_filename(self):
        payload = {"parts": [{"mimeType": "text/html"}]}
        assert _has_attachments(payload) is False


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------


class TestAuth:
    def test_refresh_token_called_on_first_request(self, client, mock_http):
        client._access_token = None
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "new-token"}
        token_resp.raise_for_status = MagicMock()

        labels_resp = MagicMock()
        labels_resp.status_code = 200
        labels_resp.json.return_value = {"labels": []}
        labels_resp.raise_for_status = MagicMock()

        mock_http.post.return_value = token_resp
        mock_http.request.return_value = labels_resp

        client._load_labels()
        # Token was refreshed
        assert client._access_token == "new-token"

    def test_retry_on_401(self, client, mock_http):
        """First request returns 401 → refresh → retry succeeds."""
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "refreshed"}
        token_resp.raise_for_status = MagicMock()
        mock_http.post.return_value = token_resp

        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"labels": []}
        resp_ok.raise_for_status = MagicMock()

        mock_http.request.side_effect = [resp_401, resp_ok]
        client._load_labels()
        assert mock_http.request.call_count == 2


# ------------------------------------------------------------------
# Labels
# ------------------------------------------------------------------


class TestLabels:
    def test_ensure_label_creates_when_missing(self, client, mock_http):
        # _load_labels returns empty
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"labels": [{"name": "INBOX", "id": "INBOX"}]}
        list_resp.raise_for_status = MagicMock()

        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"id": "Label_99", "name": "RESPONDIDO IA"}
        create_resp.raise_for_status = MagicMock()

        mock_http.request.side_effect = [list_resp, create_resp]

        label_id = client._ensure_label("RESPONDIDO IA")
        assert label_id == "Label_99"
        assert client._label_cache["RESPONDIDO IA"] == "Label_99"

    def test_ensure_label_reuses_cached(self, client):
        client._label_cache = {"RESPONDIDO IA": "Label_42"}
        assert client._ensure_label("RESPONDIDO IA") == "Label_42"


# ------------------------------------------------------------------
# update_email
# ------------------------------------------------------------------


class TestUpdateEmail:
    def test_mark_read(self, client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.update_email("test@gmail.com", "msg1", is_read=True)
        call_args = mock_http.request.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "UNREAD" in body["removeLabelIds"]

    def test_mark_unread(self, client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.update_email("test@gmail.com", "msg1", is_read=False)
        call_args = mock_http.request.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "UNREAD" in body["addLabelIds"]

    def test_add_categories_resolves_labels(self, client, mock_http):
        client._label_cache = {"RESPONDIDO IA": "Label_42"}
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.update_email(
            "test@gmail.com", "msg1", is_read=True, add_categories=["RESPONDIDO IA"]
        )
        call_args = mock_http.request.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "Label_42" in body["addLabelIds"]


# ------------------------------------------------------------------
# move_email
# ------------------------------------------------------------------


class TestMoveEmail:
    def test_move_adds_label_removes_inbox(self, client, mock_http):
        client._label_cache = {"Archivo": "Label_10"}
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.move_email("test@gmail.com", "msg1", "Archivo")
        call_args = mock_http.request.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "Label_10" in body["addLabelIds"]
        assert "INBOX" in body["removeLabelIds"]

    def test_move_with_parent_folder(self, client, mock_http):
        client._label_cache = {"Clientes/Activos": "Label_20"}
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.move_email("test@gmail.com", "msg1", "Activos", parent_folder="Clientes")
        call_args = mock_http.request.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "Label_20" in body["addLabelIds"]


# ------------------------------------------------------------------
# delete_email
# ------------------------------------------------------------------


class TestDeleteEmail:
    def test_trash(self, client, mock_http):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_http.request.return_value = resp

        client.delete_email("test@gmail.com", "msg1")
        call_args = mock_http.request.call_args
        assert "/messages/msg1/trash" in call_args[0][1]


# ------------------------------------------------------------------
# reply_to_email
# ------------------------------------------------------------------


class TestReplyToEmail:
    def _setup_meta_mock(self, mock_http):
        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {
            "threadId": "thread_1",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<orig@mail.gmail.com>"},
                    {"name": "References", "value": ""},
                    {"name": "Subject", "value": "Original"},
                    {"name": "From", "value": "sender@example.com"},
                ]
            },
        }
        meta_resp.raise_for_status = MagicMock()

        send_resp = MagicMock()
        send_resp.status_code = 200
        send_resp.raise_for_status = MagicMock()

        mock_http.request.side_effect = [meta_resp, send_resp]

    def test_reply_sends(self, client, mock_http):
        self._setup_meta_mock(mock_http)
        client.reply_to_email("me@gmail.com", "msg1", "<p>Gracias</p>", "Re: Original")

        send_call = mock_http.request.call_args_list[1]
        assert "/messages/send" in send_call[0][1]
        body = send_call.kwargs.get("json") or send_call[1].get("json")
        assert body["threadId"] == "thread_1"

    def test_reply_draft_mode(self, mock_http):
        client = _make_client(draft_mode=True)
        client._http = mock_http
        client._access_token = "fake"

        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {
            "threadId": "thread_1",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<orig@mail.gmail.com>"},
                    {"name": "References", "value": ""},
                    {"name": "Subject", "value": "Original"},
                    {"name": "From", "value": "sender@example.com"},
                ]
            },
        }
        meta_resp.raise_for_status = MagicMock()

        draft_resp = MagicMock()
        draft_resp.status_code = 200
        draft_resp.raise_for_status = MagicMock()

        mock_http.request.side_effect = [meta_resp, draft_resp]
        client.reply_to_email("me@gmail.com", "msg1", "<p>Draft</p>", "Re: Orig")

        draft_call = mock_http.request.call_args_list[1]
        assert "/drafts" in draft_call[0][1]

    def test_reply_with_override_to(self, client, mock_http):
        self._setup_meta_mock(mock_http)
        client.reply_to_email(
            "me@gmail.com",
            "msg1",
            "<p>Override</p>",
            "Re: Original",
            override_to={"name": "Other", "address": "other@example.com"},
        )
        send_call = mock_http.request.call_args_list[1]
        body = send_call.kwargs.get("json") or send_call[1].get("json")
        raw_bytes = base64.urlsafe_b64decode(body["raw"])
        assert b"other@example.com" in raw_bytes

    def test_reply_uses_send_as(self, mock_http):
        client = _make_client(send_as="alias@midominio.com")
        client._http = mock_http
        client._access_token = "fake"

        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.json.return_value = {
            "threadId": "t1",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<x>"},
                    {"name": "References", "value": ""},
                    {"name": "Subject", "value": "S"},
                    {"name": "From", "value": "original@example.com"},
                ]
            },
        }
        meta_resp.raise_for_status = MagicMock()

        send_resp = MagicMock()
        send_resp.status_code = 200
        send_resp.raise_for_status = MagicMock()
        mock_http.request.side_effect = [meta_resp, send_resp]

        client.reply_to_email("me@gmail.com", "msg1", "<p>Alias</p>", "Re: S")
        send_call = mock_http.request.call_args_list[1]
        body = send_call.kwargs.get("json") or send_call[1].get("json")
        raw_bytes = base64.urlsafe_b64decode(body["raw"])
        assert b"alias@midominio.com" in raw_bytes


# ------------------------------------------------------------------
# Normalisation is compatible with actions.already_processed
# ------------------------------------------------------------------


class TestCompatWithActions:
    def test_normalised_msg_works_with_already_processed(self):
        from gmail_inbox_bot.actions import already_processed

        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert already_processed(msg) is False

        msg["labels"] = ["RESPONDIDO IA"]
        msg["categories"] = ["RESPONDIDO IA"]
        assert already_processed(msg) is True

    def test_label_ids_converted_to_names_for_already_processed(self):
        """Label IDs like 'Label_123' must be converted to names so that
        already_processed() can match them against PROCESSED_TAGS."""
        from gmail_inbox_bot.actions import already_processed

        id_to_name = {"Label_abc123": "REVISAR IA"}
        raw = {**RAW_GMAIL_MESSAGE, "labelIds": ["INBOX", "UNREAD", "Label_abc123"]}
        msg = _normalise_message(raw, id_to_name=id_to_name)
        assert "REVISAR IA" in msg["labels"]
        assert already_processed(msg) is True

    def test_label_ids_without_mapping_stay_as_ids(self):
        """Without a reverse mapping, label IDs are kept as-is (backward compat)."""
        raw = {**RAW_GMAIL_MESSAGE, "labelIds": ["INBOX", "Label_xyz"]}
        msg = _normalise_message(raw)
        assert "Label_xyz" in msg["labels"]

    def test_normalised_msg_has_all_fields_for_execute(self):
        """Ensure the normalised payload has every key that execute() reads."""
        msg = _normalise_message(RAW_GMAIL_MESSAGE)
        assert "id" in msg
        assert "subject" in msg
        assert "from" in msg
        assert "emailAddress" in msg["from"]
        assert "body" in msg
        assert "content" in msg["body"]
        assert "categories" in msg
        assert "receivedDateTime" in msg
