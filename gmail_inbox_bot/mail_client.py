"""MailClient protocol — provider-agnostic contract for email operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MailClient(Protocol):
    """Contract that actions.py and mail_processing.py expect from any email backend.

    Every method mirrors the call-sites already present in the codebase.
    Implementations must also expose a ``draft_mode`` bool attribute.
    """

    draft_mode: bool

    # --- Read ---

    def get_unread_emails(self, user_email: str, *, top: int = 50) -> list[dict]:
        """Return up to *top* unread messages as normalised dicts.

        Each dict must contain at least::

            {
                "id":               str,
                "threadId":         str,
                "subject":          str,
                "from":             {"emailAddress": {"name": str, "address": str}},
                "sender":           {"emailAddress": {"name": str, "address": str}},
                "body":             {"content": str},   # HTML preferred
                "hasAttachments":   bool,
                "labels":           list[str],
                "categories":       list[str],          # mirror of labels for compat
                "receivedDateTime": str,                 # ISO-8601
                "internetMessageId": str,               # Message-ID header
            }
        """
        ...

    # --- Update / Labels ---

    def update_email(
        self,
        user_email: str,
        message_id: str,
        is_read: bool = True,
        add_categories: list[str] | None = None,
    ) -> None:
        """Mark read/unread and optionally add labels (categories)."""
        ...

    # --- Move ---

    def move_email(
        self,
        user_email: str,
        message_id: str,
        folder_name: str,
        parent_folder: str | None = None,
    ) -> None:
        """Apply *folder_name* label and remove INBOX."""
        ...

    # --- Delete ---

    def delete_email(self, user_email: str, message_id: str) -> None:
        """Move message to trash."""
        ...

    # --- Reply ---

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
        """Reply (or create draft reply) in the correct thread."""
        ...

    # --- Reply with attachment ---

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
        """Reply with file attachments (or create draft)."""
        ...

    # --- Forward ---

    def forward_email(
        self,
        user_email: str,
        message_id: str,
        to_name: str,
        to_address: str,
        *,
        body_prefix: str = "",
    ) -> None:
        """Forward message (or create draft forward)."""
        ...
