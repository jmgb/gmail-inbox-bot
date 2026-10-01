"""Reusable mail-processing helpers extracted from the original bot."""

import html as html_lib
import re

from .actions import PROCESSED_TAGS, TAG_PENDING_MANAGE
from .ib_trades import notify_trade, parse_trade
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.mail_processing", "logs/app.log")


def build_poll_query(base_query: str) -> str:
    """Añade a *base_query* la exclusión de los emails que ya llevan un tag de procesado.

    Las categorías que se quedan en el inbox (``personal``, ``finanzas``, ``otros``) siguen
    sin leer a propósito, así que ``is:unread in:inbox`` las devuelve en cada poll y el bot
    se descargaba el mensaje completo para luego saltarlo en ``already_processed()``. Con la
    exclusión el filtrado lo hace Gmail y no baja nada. ``already_processed()`` se mantiene
    como red de seguridad: si un label se renombra o alguien sobreescribe la query, el bot
    sigue sin reprocesar.

    Se deriva de ``PROCESSED_TAGS`` para que un tag nuevo entre solo, sin lista paralela.
    """
    query = base_query
    for tag in sorted(PROCESSED_TAGS):
        exclusion = f'-label:"{tag}"'
        if exclusion not in query:
            query = f"{query} {exclusion}"
    return query


PRE_FILTER_ACTIONS = frozenset({"silent", "tag", "tag_and_move", "delete", "ib_trade"})


def apply_pre_filters(
    mail_client, config: dict, email_msg: dict, dry_run: bool
) -> tuple[str, str] | None:
    """Evaluate pre-filters in order and execute the first matching action.

    Returns (result_description, filter_name) or None if no filter matched.
    """
    filters = config.get("pre_filters", [])
    if not filters:
        return None

    sender_address = email_msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    subject = email_msg.get("subject", "").lower()
    user_email = config["email"]
    msg_id = email_msg["id"]

    for pre_filter in filters:
        match = pre_filter.get("match", {})
        matched = True

        if "sender_contains" in match:
            needles = match["sender_contains"]
            if isinstance(needles, str):
                needles = [needles]
            if not any(needle.lower() in sender_address for needle in needles):
                matched = False

        if matched and "sender_not_contains" in match:
            needles = match["sender_not_contains"]
            if isinstance(needles, str):
                needles = [needles]
            if any(needle.lower() in sender_address for needle in needles):
                matched = False

        if matched and "subject_contains" in match:
            needles = match["subject_contains"]
            if isinstance(needles, str):
                needles = [needles]
            if not any(needle.lower() in subject for needle in needles):
                matched = False

        if matched and "subject_not_contains" in match:
            needles = match["subject_not_contains"]
            if isinstance(needles, str):
                needles = [needles]
            if any(needle.lower() in subject for needle in needles):
                matched = False

        if not matched:
            continue

        action = pre_filter.get("action", "silent")
        name = pre_filter.get("name", "unnamed filter")

        if dry_run:
            return f"[DRY-RUN] pre-filter '{name}' -> {action}", name

        if action == "silent":
            mail_client.update_email(user_email, msg_id, is_read=True)
            return f"pre-filter '{name}' -> silent", name

        if action == "tag":
            tag = pre_filter.get("tag", TAG_PENDING_MANAGE)
            mail_client.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
            return f"pre-filter '{name}' -> tag {tag}", name

        if action == "tag_and_move":
            tag = pre_filter.get("tag", TAG_PENDING_MANAGE)
            folder = pre_filter.get("folder", "")
            parent_folder = config.get("parent_folder")
            mail_client.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
            if folder:
                mail_client.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
            return f"pre-filter '{name}' -> tag {tag} + move '{folder}'", name

        if action == "delete":
            mail_client.delete_email(user_email, msg_id)
            return f"pre-filter '{name}' -> delete", name

        if action == "ib_trade":
            original_subject = email_msg.get("subject", "")
            trade = parse_trade(original_subject)
            folder = pre_filter.get("folder", "")
            parent_folder = config.get("parent_folder")
            if trade:
                notify_trade(trade, user_email)
                log.info(
                    "[%s] IB trade: %s %s %s @ %.4f (%s)",
                    msg_id,
                    trade.side,
                    trade.quantity,
                    trade.ticker,
                    trade.price,
                    trade.account,
                )
            else:
                log.warning("[%s] IB trade subject not parseable: %s", msg_id, original_subject)
            mail_client.update_email(user_email, msg_id, is_read=True)
            if folder:
                mail_client.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
            return f"pre-filter '{name}' -> ib_trade ({original_subject})", name

    return None


_INVISIBLE_BLOCKS = re.compile(
    r"<!--.*?-->|<(head|style|script|title)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_BLOCK_END = re.compile(r"</(?:p|div|tr|li|h[1-6]|table|blockquote)\s*>", re.IGNORECASE)


def strip_html(raw: str) -> str:
    """Convert HTML to plain text using stdlib only.

    El texto resultante es lo que leen los clasificadores, y Jev solo ve los primeros 6000
    caracteres: el CSS de ``<style>``, los ``<script>``, los comentarios condicionales de
    Outlook y la indentación de las plantillas de marketing se comían ese presupuesto antes
    de llegar al contenido. Los cierres de bloque pasan a salto de línea para que dos
    párrafos no queden pegados en una sola palabra.
    """
    text = _INVISIBLE_BLOCKS.sub("", raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_forwarded_email(sender_address: str, config: dict) -> bool:
    """Check if sender matches any configured forwarded-from pattern."""
    forwarded_from = config.get("forwarded_from", [])
    if not forwarded_from:
        return False
    sender_lower = sender_address.lower()
    return any(pattern.lower() in sender_lower for pattern in forwarded_from)


_FWD_PATTERNS = [
    re.compile(
        r"<b>\s*(?:De|From)\s*:\s*</b>\s*([^&<]*?)\s*&lt;([^&\s]+@[^&\s]+?)&gt;",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:De|From)\s*:\s*([^&<]*?)\s*&lt;([^&\s]+@[^&\s]+?)&gt;",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:De|From)\s*:\s*(.+?)\s*<([^>\s]+@[^>\s]+?)>",
        re.IGNORECASE,
    ),
]


def extract_original_sender(body_html: str) -> dict | None:
    """Extract original sender from a forwarded email body."""
    for pattern in _FWD_PATTERNS:
        match = pattern.search(body_html)
        if not match:
            continue
        name = html_lib.unescape(match.group(1).strip())
        address = html_lib.unescape(match.group(2).strip().lower())
        if "@" in address and "." in address.split("@")[-1]:
            return {"name": name, "address": address}
    return None
