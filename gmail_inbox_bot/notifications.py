"""Telegram notifications for important email classifications."""

from __future__ import annotations

from .telegram import enviar_mensaje_telegram


def notify_important_email(
    *,
    mailbox: str,
    categoria: str,
    sender: str,
    subject: str,
    razon: str = "",
) -> None:
    """Send a Telegram notification when the bot classifies an important email.

    Called for categories that the user wants to be alerted about
    (e.g. ``personal``, ``finanzas``).
    """
    emoji = {"personal": "\U0001f4e9", "finanzas": "\U0001f4b0"}.get(categoria, "\u2757")
    lines = [
        f"{emoji} <b>Email importante ({categoria})</b>",
        f"<b>Buzón:</b> {mailbox}",
        f"<b>De:</b> {sender}",
        f"<b>Asunto:</b> {subject}",
    ]
    if razon:
        lines.append(f"<b>Razón:</b> {razon}")
    enviar_mensaje_telegram("\n".join(lines), referencia="notify_important")


# Categories that trigger a Telegram notification
NOTIFY_CATEGORIES: frozenset[str] = frozenset({"personal", "finanzas"})
