"""Polling bot — reads Gmail, classifies, and executes actions."""

from __future__ import annotations

import os
import time

from openai import OpenAI

from .actions import already_processed, execute
from .classifier import DEFAULT_MODEL, GPT_OSS_120B, classify_email, load_prompt
from .config import load_env, load_mailbox_configs
from .gmail_client import GmailClient
from .logger import setup_logger
from .mail_processing import (
    _is_forwarded_email,
    apply_pre_filters,
    extract_original_sender,
    strip_html,
)
from .metrics import record_email
from .notifications import NOTIFY_CATEGORIES, notify_important_email
from .sheets import build_sheets_client
from .telegram_logger import setup_telegram_logging

log = setup_logger("gmail_inbox_bot.bot", "logs/app.log")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODELS = {
    GPT_OSS_120B,
    "openai/gpt-oss-20b",
}


def _build_gmail_client(
    env: dict[str, str],
    mailbox_config: dict,
    *,
    draft_mode: bool = False,
) -> GmailClient:
    """Build a GmailClient for a specific mailbox.

    The refresh token comes from the mailbox YAML (``refresh_token_env``
    points to the .env variable name, e.g. ``GOOGLE_REFRESH_TOKEN_JESUS82C``).
    """
    token_var = mailbox_config.get("refresh_token_env", "")
    refresh_token = os.environ.get(token_var, "") if token_var else ""
    if not refresh_token:
        raise RuntimeError(
            f"Mailbox '{mailbox_config.get('name')}': "
            f"refresh_token_env='{token_var}' not found or empty in environment"
        )
    return GmailClient(
        client_id=env["GOOGLE_CLIENT_ID"],
        client_secret=env["GOOGLE_CLIENT_SECRET"],
        refresh_token=refresh_token,
        send_as=mailbox_config.get("send_as") or None,
        draft_mode=draft_mode,
    )


def _build_llm_clients(env: dict[str, str]) -> dict[str, OpenAI | None]:
    clients: dict[str, OpenAI | None] = {"openai": None, "groq": None}

    openai_api_key = env.get("OPENAI_API_KEY")
    if openai_api_key:
        clients["openai"] = OpenAI(api_key=openai_api_key)
    else:
        log.warning("OPENAI_API_KEY not set")

    groq_api_key = env.get("GROQ_API_KEY")
    if groq_api_key:
        clients["groq"] = OpenAI(base_url=GROQ_BASE_URL, api_key=groq_api_key)
    else:
        log.warning("GROQ_API_KEY not set")

    return clients


def _select_llm_client(client_or_clients, model: str):
    if not isinstance(client_or_clients, dict):
        return client_or_clients

    if model in GROQ_MODELS:
        return client_or_clients.get("groq")

    return client_or_clients.get("openai")


def _enrich_forwarded(email_msg: dict, config: dict) -> None:
    """Detect forwarded emails and enrich with original sender metadata."""
    sender_address = email_msg.get("from", {}).get("emailAddress", {}).get("address", "")
    if not _is_forwarded_email(sender_address, config):
        return

    body_html = email_msg.get("body", {}).get("content", "")
    original = extract_original_sender(body_html)
    if original:
        email_msg["_original_sender"] = original
    else:
        email_msg["_forward_extraction_failed"] = True


def _process_email(
    gmail: GmailClient,
    openai_client: OpenAI | None,
    config: dict,
    email_msg: dict,
    *,
    dry_run: bool = False,
) -> str:
    """Process a single email through the full pipeline. Returns status string."""
    msg_id = email_msg["id"]
    subject = email_msg.get("subject", "")[:80]
    sender = email_msg.get("from", {}).get("emailAddress", {}).get("address", "?")

    # 1. Idempotency check
    if already_processed(email_msg):
        return f"skipped (already processed) — {subject}"

    mailbox_name = config.get("name", config.get("email", ""))

    # 2. Pre-filters
    pre_match = apply_pre_filters(gmail, config, email_msg, dry_run)
    if pre_match:
        pre_result, filter_name = pre_match
        log.info("[%s] %s | De: %s | Asunto: %s", msg_id, pre_result, sender, subject)
        record_email(
            mailbox=mailbox_name,
            category=f"pre_filter:{filter_name}",
            action=pre_result,
            msg_id=msg_id,
            sender=sender,
            subject=subject,
        )
        return pre_result

    # 3. Enrich forwarded emails
    _enrich_forwarded(email_msg, config)

    # 4. Classify
    model = config.get("classifier", {}).get("model", "") or DEFAULT_MODEL
    llm_client = _select_llm_client(openai_client, model)
    if not llm_client:
        log.warning("[%s] No OpenAI client — tagging ERROR IA", msg_id)
        gmail.update_email(
            config["email"],
            msg_id,
            is_read=False,
            add_categories=["ERROR IA"],
        )
        record_email(
            mailbox=mailbox_name,
            category="error_no_classifier",
            action="tag:ERROR IA",
            msg_id=msg_id,
            error=True,
            sender=sender,
            subject=subject,
        )
        return "no classifier available — tagged ERROR IA"

    body_html = email_msg.get("body", {}).get("content", "")
    body_text = strip_html(body_html)
    sender_name = email_msg.get("from", {}).get("emailAddress", {}).get("name", "")
    has_attachments = email_msg.get("hasAttachments", False)

    prompt_file = config.get("classifier", {}).get("prompt_file", "")
    if not prompt_file:
        log.error("[%s] No classifier.prompt_file in config", msg_id)
        gmail.update_email(
            config["email"],
            msg_id,
            is_read=False,
            add_categories=["ERROR IA"],
        )
        record_email(
            mailbox=mailbox_name,
            category="error_no_prompt",
            action="tag:ERROR IA",
            msg_id=msg_id,
            error=True,
            sender=sender,
            subject=subject,
        )
        return "error — no prompt_file configured, tagged ERROR IA"

    system_prompt = load_prompt(prompt_file)
    classification = classify_email(
        llm_client,
        system_prompt,
        subject,
        body_text,
        sender_name,
        sender,
        has_attachments,
        model=model,
    )

    if not classification:
        log.error("[%s] Classification failed — De: %s | Asunto: %s", msg_id, sender, subject)
        gmail.update_email(config["email"], msg_id, is_read=False, add_categories=["ERROR IA"])
        record_email(
            mailbox=mailbox_name,
            category="error_clasificacion",
            action="tag:ERROR IA",
            msg_id=msg_id,
            model=model,
            error=True,
            sender=sender,
            subject=subject,
        )
        return "classification failed — tagged ERROR IA"

    # 5. Notify important emails
    categoria = classification.get("categoria", "")
    if categoria in NOTIFY_CATEGORIES:
        notify_important_email(
            mailbox=config.get("email", ""),
            categoria=categoria,
            sender=f"{sender_name} <{sender}>" if sender_name else sender,
            subject=subject,
            razon=classification.get("razon_clasificacion", ""),
        )

    # 6. Execute action
    result = execute(
        gmail,
        config,
        email_msg,
        classification,
        dry_run=dry_run,
        openai_client=llm_client,
        body_text=body_text,
    )
    log.info(
        "[%s] %s | De: %s | Asunto: %s",
        msg_id,
        result,
        sender,
        subject,
    )

    # 7. Record metric
    usage = classification.get("usage") or {}
    cost = classification.get("cost") or {}
    record_email(
        mailbox=mailbox_name,
        category=categoria,
        action=result,
        msg_id=msg_id,
        model=model,
        draft_mode=gmail.draft_mode,
        classification_reason=classification.get("razon_clasificacion"),
        sender=sender,
        subject=subject,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        input_cost_usd=cost.get("input_cost_usd"),
        output_cost_usd=cost.get("output_cost_usd"),
        total_cost_usd=cost.get("total_cost_usd"),
        llm_provider=cost.get("provider"),
    )

    return result


def process_mailbox(
    gmail: GmailClient,
    openai_client: OpenAI | None,
    config: dict,
    *,
    dry_run: bool = False,
    query: str = "is:unread in:inbox",
) -> list[str]:
    """Poll one mailbox and process all unread emails. Returns list of results."""
    user_email = config["email"]
    top = config.get("max_emails_per_poll", 50)

    log.info("Polling mailbox: %s (top=%d, query=%s)", user_email, top, query)
    try:
        emails = gmail.get_unread_emails(user_email, top=top, query=query)
    except Exception:
        log.exception("Failed to fetch emails for %s", user_email)
        return ["error — failed to fetch emails"]

    if not emails:
        log.info("No unread emails for %s", user_email)
        return []

    log.info("Found %d unread email(s) for %s", len(emails), user_email)
    results: list[str] = []
    for email_msg in emails:
        try:
            result = _process_email(gmail, openai_client, config, email_msg, dry_run=dry_run)
            results.append(result)
        except Exception:
            msg_id = email_msg.get("id", "?")
            log.exception("Unhandled error processing email %s", msg_id)
            try:
                gmail.update_email(user_email, msg_id, is_read=False, add_categories=["ERROR IA"])
            except Exception:
                log.exception("Failed to tag ERROR IA on %s", msg_id)
            record_email(
                mailbox=config.get("name", user_email),
                category="error_procesamiento",
                msg_id=msg_id,
                error=True,
                sender=email_msg.get("from", {}).get("emailAddress", {}).get("address"),
                subject=email_msg.get("subject", "")[:80],
            )
            results.append(f"error — unhandled exception on {msg_id}")

    return results


def run(*, dry_run: bool = False, once: bool = False) -> None:
    """Main entry point — load config, build clients, and start polling loop.

    Parameters
    ----------
    dry_run:
        Log what would happen without making changes.
    once:
        Run a single poll cycle and exit (useful for testing/cron).
    """
    env = load_env()
    setup_telegram_logging(chat_id=os.environ.get("TELEGRAM_CHAT_ID"))
    openai_client = _build_llm_clients(env)
    configs = load_mailbox_configs()

    if not configs:
        raise RuntimeError("No mailbox configs found in config/ directory")

    # Use the shortest poll interval across all configs
    poll_interval = min(c.get("poll_interval_seconds", 600) for c in configs)

    # Build one GmailClient per mailbox (each has its own refresh token)
    clients: list[tuple[GmailClient, dict]] = []
    for config in configs:
        gmail = _build_gmail_client(env, config)
        # Inject SheetsClient if configured (for ib_trade pre-filter)
        sheets_id = config.get("sheets_id", "")
        if sheets_id:
            token_var = config.get("refresh_token_env", "")
            refresh_token = os.environ.get(token_var, "") if token_var else ""
            config["_sheets_client"] = build_sheets_client(env, refresh_token, sheets_id)
        clients.append((gmail, config))

    log.info(
        "Bot starting — %d mailbox(es), poll_interval=%ds, dry_run=%s, once=%s",
        len(clients),
        poll_interval,
        dry_run,
        once,
    )

    while True:
        for gmail, config in clients:
            query = config.get("query", "is:unread in:inbox")
            process_mailbox(gmail, openai_client, config, dry_run=dry_run, query=query)

        if once:
            log.info("Single-run mode — exiting")
            break

        log.debug("Sleeping %ds before next poll", poll_interval)
        time.sleep(poll_interval)
