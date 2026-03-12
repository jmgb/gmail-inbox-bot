"""GmailClient — Gmail API implementation of the MailClient protocol.

Covers authentication, message reading with payload normalisation,
and label-based state management (Phases 1-3 of the ROADMAP).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx

from .logger import setup_logger
from .mail_client import MailClient  # noqa: F401  (re-export for convenience)

log = setup_logger("gmail_inbox_bot.gmail_client", "logs/app.log")

BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailClient:
    """Gmail API client that satisfies the :class:`MailClient` protocol.

    Parameters
    ----------
    client_id, client_secret, refresh_token:
        OAuth2 credentials (see ``scripts/get_refresh_token.py``).
    send_as:
        Optional alias address to use as the ``From`` header.
    draft_mode:
        When *True*, write-operations create drafts instead of sending.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        send_as: str | None = None,
        draft_mode: bool = False,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.send_as = send_as
        self.draft_mode = draft_mode

        self._access_token: str | None = None
        self._http = httpx.Client(timeout=30.0)
        # name -> id cache; populated lazily
        self._label_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _refresh_access_token(self) -> str:
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self._access_token = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request, retrying once on 401."""
        url = f"{BASE_URL}{path}" if path.startswith("/") else path
        resp = self._http.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            self._refresh_access_token()
            resp = self._http.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _load_labels(self) -> None:
        """Fetch all labels and populate the name->id cache."""
        resp = self._request("GET", "/labels")
        for label in resp.json().get("labels", []):
            self._label_cache[label["name"]] = label["id"]

    def _ensure_label(self, name: str) -> str:
        """Return the label ID for *name*, creating it if necessary."""
        if not self._label_cache:
            self._load_labels()
        if name in self._label_cache:
            return self._label_cache[name]
        # Create the label
        resp = self._request(
            "POST",
            "/labels",
            json={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        label_id = resp.json()["id"]
        self._label_cache[name] = label_id
        log.info("Created Gmail label: %s -> %s", name, label_id)
        return label_id

    def _resolve_label_ids(self, names: list[str]) -> list[str]:
        """Convert a list of label names to Gmail label IDs."""
        return [self._ensure_label(n) for n in names]

    # ------------------------------------------------------------------
    # Read — Phase 2
    # ------------------------------------------------------------------

    def get_unread_emails(
        self,
        user_email: str,
        *,
        top: int = 50,
        query: str = "is:unread in:inbox",
    ) -> list[dict]:
        """List unread messages and return normalised dicts."""
        resp = self._request(
            "GET",
            "/messages",
            params={"q": query, "maxResults": top},
        )
        message_ids = resp.json().get("messages", [])

        results: list[dict] = []
        for stub in message_ids:
            detail = self._request(
                "GET",
                f"/messages/{stub['id']}",
                params={"format": "full"},
            )
            normalised = _normalise_message(detail.json())
            results.append(normalised)
        return results

    # ------------------------------------------------------------------
    # Update / Labels — Phase 3
    # ------------------------------------------------------------------

    def update_email(
        self,
        user_email: str,
        message_id: str,
        is_read: bool = True,
        add_categories: list[str] | None = None,
    ) -> None:
        add_ids: list[str] = []
        remove_ids: list[str] = []

        if is_read:
            remove_ids.append("UNREAD")
        else:
            add_ids.append("UNREAD")

        if add_categories:
            add_ids.extend(self._resolve_label_ids(add_categories))

        body: dict = {}
        if add_ids:
            body["addLabelIds"] = add_ids
        if remove_ids:
            body["removeLabelIds"] = remove_ids
        if body:
            self._request("POST", f"/messages/{message_id}/modify", json=body)

    def move_email(
        self,
        user_email: str,
        message_id: str,
        folder_name: str,
        parent_folder: str | None = None,
    ) -> None:
        """Apply *folder_name* as label + remove from INBOX."""
        label_name = f"{parent_folder}/{folder_name}" if parent_folder else folder_name
        label_id = self._ensure_label(label_name)
        self._request(
            "POST",
            f"/messages/{message_id}/modify",
            json={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
        )

    def delete_email(self, user_email: str, message_id: str) -> None:
        """Move to trash (recoverable for 30 days)."""
        self._request("POST", f"/messages/{message_id}/trash")

    # ------------------------------------------------------------------
    # Write — Phase 4
    # ------------------------------------------------------------------

    def _get_message_metadata(self, message_id: str) -> dict:
        """Fetch metadata needed for threading (Message-ID, References, threadId)."""
        resp = self._request(
            "GET",
            f"/messages/{message_id}",
            params={
                "format": "metadata",
                "metadataHeaders": ["Message-ID", "References", "Subject", "From"],
            },
        )
        data = resp.json()
        headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
        return {
            "threadId": data.get("threadId", ""),
            "messageId": headers.get("Message-ID", ""),
            "references": headers.get("References", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
        }

    def _build_reply_mime(
        self,
        meta: dict,
        html_body: str,
        subject: str,
        from_address: str,
        to_address: str,
    ) -> MIMEText:
        """Build a MIME reply message with correct threading headers."""
        msg = MIMEText(html_body, "html", "utf-8")
        msg["To"] = to_address
        msg["From"] = from_address
        msg["Subject"] = subject
        if meta["messageId"]:
            msg["In-Reply-To"] = meta["messageId"]
            refs = meta["references"]
            msg["References"] = f"{refs} {meta['messageId']}".strip()
        return msg

    def _send_or_draft(self, mime_msg, thread_id: str, force_draft: bool) -> None:
        """Send the message or create a draft depending on mode."""
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        if self.draft_mode or force_draft:
            self._request(
                "POST",
                "/drafts",
                json={"message": {"raw": raw, "threadId": thread_id}},
            )
        else:
            self._request(
                "POST",
                "/messages/send",
                json={"raw": raw, "threadId": thread_id},
            )

    def reply_to_email(
        self,
        user_email: str,
        message_id: str,
        html_body: str,
        subject: str,
        *,
        override_to: dict | None = None,
        force_draft: bool = False,
    ) -> None:
        meta = self._get_message_metadata(message_id)
        from_addr = self.send_as or user_email
        if override_to:
            to_addr = override_to["address"]
        else:
            to_addr = meta["from"]
        mime = self._build_reply_mime(meta, html_body, subject, from_addr, to_addr)
        self._send_or_draft(mime, meta["threadId"], force_draft)

    def reply_with_attachment(
        self,
        user_email: str,
        message_id: str,
        html_body: str,
        subject: str,
        attachments: list[dict],
        *,
        override_to: dict | None = None,
        force_draft: bool = False,
    ) -> None:
        meta = self._get_message_metadata(message_id)
        from_addr = self.send_as or user_email

        if override_to:
            to_addr = override_to["address"]
        else:
            to_addr = meta["from"]

        msg = MIMEMultipart()
        msg["To"] = to_addr
        msg["From"] = from_addr
        msg["Subject"] = subject
        if meta["messageId"]:
            msg["In-Reply-To"] = meta["messageId"]
            refs = meta["references"]
            msg["References"] = f"{refs} {meta['messageId']}".strip()

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for att in attachments:
            file_path = Path(att["path"])
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_path.read_bytes())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=att.get("name", file_path.name),
            )
            msg.attach(part)

        self._send_or_draft(msg, meta["threadId"], force_draft)

    def forward_email(
        self,
        user_email: str,
        message_id: str,
        to_name: str,
        to_address: str,
        *,
        body_prefix: str = "",
        body_suffix: str = "",
    ) -> None:
        # Get original message for body
        resp = self._request(
            "GET",
            f"/messages/{message_id}",
            params={"format": "full"},
        )
        original = _normalise_message(resp.json())
        original_body = original["body"]["content"]

        from_addr = self.send_as or user_email
        subject = original["subject"]
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"

        html_body = f"{body_prefix}<hr>{original_body}" if body_prefix else f"<hr>{original_body}"
        if body_suffix:
            html_body += body_suffix

        msg = MIMEText(html_body, "html", "utf-8")
        msg["To"] = f"{to_name} <{to_address}>"
        msg["From"] = from_addr
        msg["Subject"] = subject

        self._send_or_draft(msg, original.get("threadId", ""), force_draft=False)


# ------------------------------------------------------------------
# Payload normalisation
# ------------------------------------------------------------------

_FROM_RE = re.compile(r"^(.*?)\s*<([^>]+)>$")


def _parse_address(raw: str) -> dict:
    """Parse 'Name <email>' into ``{"emailAddress": {"name": ..., "address": ...}}``."""
    m = _FROM_RE.match(raw.strip())
    if m:
        name = m.group(1).strip().strip('"')
        address = m.group(2).strip()
    else:
        name = ""
        address = raw.strip()
    return {"emailAddress": {"name": name, "address": address}}


def _get_header(headers: list[dict], name: str) -> str:
    """Case-insensitive header lookup."""
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    """Walk the MIME tree and return the best body (HTML preferred, then plain)."""
    # Simple single-part message
    body_data = payload.get("body", {}).get("data")
    mime_type = payload.get("mimeType", "")

    if body_data and "text/" in mime_type:
        decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return decoded

    # Multipart — recurse into parts
    parts = payload.get("parts", [])
    html_body = ""
    plain_body = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        part_data = part.get("body", {}).get("data")

        if part_data:
            decoded = base64.urlsafe_b64decode(part_data).decode("utf-8", errors="replace")
            if part_mime == "text/html":
                html_body = decoded
            elif part_mime == "text/plain" and not plain_body:
                plain_body = decoded

        # Nested multipart (e.g. multipart/alternative inside multipart/mixed)
        if part.get("parts"):
            nested = _decode_body(part)
            if nested and not html_body:
                html_body = nested

    return html_body or plain_body


def _has_attachments(payload: dict) -> bool:
    """Check if any part has a non-empty filename (i.e. is an attachment)."""
    for part in payload.get("parts", []):
        if part.get("filename"):
            return True
        if part.get("parts") and _has_attachments(part):
            return True
    return False


def _normalise_message(raw: dict) -> dict:
    """Convert a Gmail API message (format=full) to the internal payload format."""
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])

    from_parsed = _parse_address(_get_header(headers, "From"))
    label_ids = raw.get("labelIds", [])

    # Convert internalDate (ms epoch) to ISO-8601
    internal_date_ms = raw.get("internalDate", "0")
    try:
        received_dt = datetime.fromtimestamp(
            int(internal_date_ms) / 1000, tz=timezone.utc
        ).isoformat()
    except (ValueError, OSError):
        received_dt = ""

    body_content = _decode_body(payload)

    return {
        "id": raw["id"],
        "threadId": raw.get("threadId", ""),
        "subject": _get_header(headers, "Subject"),
        "from": from_parsed,
        "sender": from_parsed,
        "body": {"content": body_content},
        "hasAttachments": _has_attachments(payload),
        "labels": list(label_ids),
        "categories": list(label_ids),
        "receivedDateTime": received_dt,
        "internetMessageId": _get_header(headers, "Message-ID"),
    }
