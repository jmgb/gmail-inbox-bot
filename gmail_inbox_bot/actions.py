"""Action router — config-driven email action dispatcher."""

import html
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .classifier import DEFAULT_MODEL, generate_response, load_prompt
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.actions", "logs/app.log")

TAG_REPLIED = "RESPONDIDO IA"
TAG_FORWARDED = "REENVIADO IA"
TAG_DRAFT_REPLY = "BORRADOR RESPUESTA IA"
TAG_DRAFT_FORWARD = "BORRADOR REENVIO IA"
TAG_PENDING_ATTACH = "PENDIENTE ADJUNTO"
TAG_PENDING_MANAGE = "PENDIENTE GESTIONAR"
TAG_ERROR = "ERROR IA"
PROCESSED_TAGS = {
    TAG_REPLIED,
    TAG_FORWARDED,
    TAG_DRAFT_REPLY,
    TAG_DRAFT_FORWARD,
    TAG_PENDING_ATTACH,
    TAG_PENDING_MANAGE,
    TAG_ERROR,
}

# --- Forwarded email support ---

FORWARD_WARNING_HTML = (
    '<div style="background:#fff3cd;border:1px solid #ffc107;padding:10px;'
    'margin-bottom:10px;border-radius:4px;">'
    "<b>&#9888;&#65039; EMAIL REENVIADO &mdash; VERIFICAR DESTINATARIO</b><br>"
    "Este email fue reenviado desde <b>{sender}</b>. "
    "No se pudo extraer el remitente original autom&aacute;ticamente. "
    "Por favor, verifica el destinatario antes de enviar."
    "</div>"
)


def _forward_reply_params(email_msg: dict) -> tuple[dict | None, bool]:
    """Extract forwarded-email reply overrides from enriched email_msg.

    Returns:
        (override_to, force_draft)
        - override_to: {"name": ..., "address": ...} when original sender was extracted.
        - force_draft: True when the email is a forward but extraction failed
                       (forces draft creation with a warning banner).
    """
    original = email_msg.get("_original_sender")
    if original:
        return original, False
    if email_msg.get("_forward_extraction_failed"):
        return None, True
    return None, False


def _apply_forward_override(html_body: str, email_msg: dict) -> tuple[str, dict | None, bool]:
    """Apply forwarded-email handling to a reply body.

    Returns (html_body, override_to, force_draft).
    If extraction failed, prepends a warning banner to html_body.
    """
    override_to, force_draft = _forward_reply_params(email_msg)
    if force_draft:
        fwd_sender = email_msg.get("from", {}).get("emailAddress", {}).get("address", "?")
        html_body = FORWARD_WARNING_HTML.format(sender=html.escape(fwd_sender)) + html_body
    return html_body, override_to, force_draft


def _reply_mode_str(draft_mode: bool, override_to: dict | None, force_draft: bool) -> str:
    """Human-readable mode string for log/result messages."""
    if force_draft:
        return "draft reply (forward fallback)"
    if draft_mode:
        return "draft reply"
    return "replied"


def already_processed(email_msg: dict) -> bool:
    markers = email_msg.get("labels") or email_msg.get("categories", [])
    return bool(set(markers) & PROCESSED_TAGS)


def _plain_to_html(text: str) -> str:
    escaped = html.escape(text)
    return escaped.replace("\n", "<br>\n")


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIGNATURE_FILE = str(_PROJECT_ROOT / "templates" / "signature.html")

# Cache: path -> html content
_signature_cache: dict[str, str] = {}


def _load_signature(config: dict) -> str:
    sig_file = config.get("signature_file", DEFAULT_SIGNATURE_FILE)
    if not sig_file:
        return ""
    if sig_file in _signature_cache:
        return _signature_cache[sig_file]
    try:
        sig_html = Path(sig_file).read_text(encoding="utf-8")
        result = "<br><br>" + sig_html
    except FileNotFoundError:
        log.warning("Signature file not found: %s", sig_file)
        result = ""
    _signature_cache[sig_file] = result
    return result


def _reply_tag(draft_mode: bool) -> str:
    return TAG_DRAFT_REPLY if draft_mode else TAG_REPLIED


def _today_in_madrid() -> date:
    return datetime.now(ZoneInfo("Europe/Madrid")).date()


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        log.warning("Invalid template date format: %s", value)
        return None


def _variant_is_active(variant: dict, today: date) -> bool:
    valid_from = variant.get("valid_from")
    valid_until = variant.get("valid_until")

    start = _parse_iso_date(valid_from) if isinstance(valid_from, str) else None
    end = _parse_iso_date(valid_until) if isinstance(valid_until, str) else None

    if isinstance(valid_from, str) and start is None:
        return False
    if isinstance(valid_until, str) and end is None:
        return False
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


def _resolve_template_variant(template: dict) -> dict:
    variants = template.get("variants")
    if not isinstance(variants, list) or not variants:
        return template

    today = _today_in_madrid()
    for variant in variants:
        if isinstance(variant, dict) and _variant_is_active(variant, today):
            return variant

    default_template = template.get("default")
    if isinstance(default_template, dict):
        return default_template
    return template


def _get_template_body(config, classification):
    categoria = classification.get("categoria", "otros")
    idioma = classification.get("idioma", "")
    tpl = config.get("templates", {}).get(categoria, {})
    if isinstance(tpl, dict):
        tpl = _resolve_template_variant(tpl)
    else:
        tpl = {}
    lang_key = "pt" if idioma == "portugués" else "esp"
    body_text = tpl.get(lang_key, tpl.get("esp", ""))
    return body_text, lang_key


def _reply_subject(email_msg):
    subject = email_msg.get("subject", "")
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _classification_banner(classification: dict, is_draft: bool) -> str:
    """Build an HTML banner with the AI classification reason for draft review.

    Only returns content when is_draft=True, so the banner never leaks
    into emails that are actually sent.
    """
    if not is_draft:
        return ""
    categoria = classification.get("categoria", "")
    razon = classification.get("razon_clasificacion", "")
    if not razon and not categoria:
        return ""
    parts: list[str] = []
    if categoria:
        parts.append(f"<b>Categor\u00eda:</b> {html.escape(categoria)}")
    if razon:
        parts.append(f"<b>Motivo:</b> {html.escape(razon)}")
    return (
        '<div style="background:#e8f4fd;border:1px solid #0078d4;padding:12px;'
        "margin-bottom:12px;border-radius:4px;font-family:Calibri,Arial,sans-serif;"
        'font-size:13px;">'
        "<b>&#x1F916; Clasificaci\u00f3n IA</b><br>" + "<br>".join(parts) + "</div>"
    )


# --- Handlers ---


def _handle_forward(graph, user_email, msg_id, rule, config, classification, dry_run):
    dest = rule["destination"]
    if dry_run:
        return f"[DRY-RUN] forward -> {dest['address']}"
    banner = _classification_banner(classification, graph.draft_mode)
    signature = _load_signature(config)
    graph.forward_email(
        user_email,
        msg_id,
        dest["name"],
        dest["address"],
        body_prefix=banner,
        body_suffix=signature,
    )
    tag = TAG_DRAFT_FORWARD if graph.draft_mode else TAG_FORWARDED
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    mode = "draft forward" if graph.draft_mode else "forwarded"
    return f"{mode} -> {dest['address']}"


def _handle_reply(graph, user_email, msg_id, email_msg, config, classification, dry_run):
    categoria = classification.get("categoria", "otros")
    body_text, lang_key = _get_template_body(config, classification)
    if not body_text:
        log.warning("No template for categoria=%s, tagging PENDIENTE GESTIONAR", categoria)
        return _handle_tag(
            graph, user_email, msg_id, {"tag": TAG_PENDING_MANAGE}, dry_run, categoria
        )

    signature = _load_signature(config)
    html_body = _plain_to_html(body_text) + signature
    html_body, override_to, force_draft = _apply_forward_override(html_body, email_msg)
    banner = _classification_banner(classification, graph.draft_mode or force_draft)
    if banner:
        html_body = banner + html_body

    if dry_run:
        preview = body_text[:80].replace("\n", " ")
        return f"[DRY-RUN] reply ({categoria}, {lang_key}): {preview}..."

    graph.reply_to_email(
        user_email,
        msg_id,
        html_body,
        _reply_subject(email_msg),
        override_to=override_to,
        force_draft=force_draft,
    )
    tag = TAG_DRAFT_REPLY if force_draft else _reply_tag(graph.draft_mode)
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    mode = _reply_mode_str(graph.draft_mode, override_to, force_draft)
    dest = f" → {override_to['address']}" if override_to else ""
    return f"{mode}{dest} ({categoria}, {lang_key})"


def _handle_reply_with_attachment(
    graph, user_email, msg_id, email_msg, config, classification, rule, dry_run
):
    categoria = classification.get("categoria", "otros")
    body_text, lang_key = _get_template_body(config, classification)
    attachments = rule.get("attachments", [])

    if dry_run:
        names = [a["name"] for a in attachments]
        return f"[DRY-RUN] reply+attach ({categoria}): {names}"

    signature = _load_signature(config)
    html_body = _plain_to_html(body_text) + signature
    html_body, override_to, force_draft = _apply_forward_override(html_body, email_msg)
    banner = _classification_banner(classification, graph.draft_mode or force_draft)
    if banner:
        html_body = banner + html_body
    graph.reply_with_attachment(
        user_email,
        msg_id,
        html_body,
        _reply_subject(email_msg),
        attachments,
        override_to=override_to,
        force_draft=force_draft,
    )
    tag = TAG_DRAFT_REPLY if force_draft else _reply_tag(graph.draft_mode)
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    mode = _reply_mode_str(graph.draft_mode, override_to, force_draft)
    dest = f" → {override_to['address']}" if override_to else ""
    return f"{mode}+attach{dest} ({categoria}, {len(attachments)} files)"


def _handle_dynamic_reply(
    graph,
    user_email,
    msg_id,
    email_msg,
    config,
    classification,
    rule,
    openai_client,
    body_text,
    dry_run,
):
    model = rule.get("model", DEFAULT_MODEL)
    prompt_file = rule.get("response_prompt_file")

    if not prompt_file or not openai_client:
        _subj = email_msg.get("subject", "?")[:80]
        _sender = email_msg.get("from", {}).get("emailAddress", {}).get("address", "?")
        _received = email_msg.get("receivedDateTime", "?")
        log.error(
            "[%s] dynamic_reply missing prompt_file or openai_client "
            "— De: %s | Asunto: %s | Fecha: %s",
            user_email,
            _sender,
            _subj,
            _received,
        )
        return _handle_tag(
            graph,
            user_email,
            msg_id,
            {"tag": TAG_PENDING_MANAGE},
            dry_run,
            "dynamic_reply",
        )

    if dry_run:
        return f"[DRY-RUN] dynamic_reply (model={model})"

    system_prompt = load_prompt(prompt_file)
    # For dynamic replies on forwarded emails, use the original sender name if available
    original_sender = email_msg.get("_original_sender")
    if original_sender:
        sender_name = original_sender.get("name", "")
    else:
        sender_name = email_msg.get("from", {}).get("emailAddress", {}).get("name", "")
    response_text = generate_response(
        openai_client, system_prompt, body_text, sender_name, model=model
    )

    if not response_text:
        _subj = email_msg.get("subject", "?")[:80]
        _sender = email_msg.get("from", {}).get("emailAddress", {}).get("address", "?")
        _received = email_msg.get("receivedDateTime", "?")
        log.error(
            "[%s] dynamic_reply: OpenAI devolvió respuesta vacía — De: %s | Asunto: %s | Fecha: %s",
            user_email,
            _sender,
            _subj,
            _received,
        )
        graph.update_email(user_email, msg_id, is_read=False, add_categories=[TAG_ERROR])
        return "dynamic_reply failed, tagged ERROR IA"

    signature = _load_signature(config)
    html_body = _plain_to_html(response_text) + signature
    html_body, override_to, force_draft = _apply_forward_override(html_body, email_msg)
    banner = _classification_banner(classification, graph.draft_mode or force_draft)
    if banner:
        html_body = banner + html_body
    graph.reply_to_email(
        user_email,
        msg_id,
        html_body,
        _reply_subject(email_msg),
        override_to=override_to,
        force_draft=force_draft,
    )
    tag = TAG_DRAFT_REPLY if force_draft else _reply_tag(graph.draft_mode)
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    mode = _reply_mode_str(graph.draft_mode, override_to, force_draft)
    dest = f" → {override_to['address']}" if override_to else ""
    return f"{mode}{dest} dynamic (model={model})"


def _handle_silent(graph, user_email, msg_id, dry_run, categoria):
    if dry_run:
        return f"[DRY-RUN] silent ({categoria})"
    graph.update_email(user_email, msg_id, is_read=True)
    return f"silent ({categoria})"


def _handle_delete(graph, user_email, msg_id, dry_run, categoria):
    if dry_run:
        return f"[DRY-RUN] delete ({categoria})"
    graph.delete_email(user_email, msg_id)
    return f"deleted ({categoria})"


def _handle_move(graph, user_email, msg_id, rule, dry_run, categoria, parent_folder=None):
    folder = rule.get("folder", "")
    if dry_run:
        return f"[DRY-RUN] move -> '{folder}' ({categoria})"
    graph.update_email(user_email, msg_id, is_read=True)
    graph.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
    return f"moved -> '{folder}' ({categoria})"


def _handle_tag(graph, user_email, msg_id, rule, dry_run, categoria):
    tag = rule.get("tag", TAG_PENDING_MANAGE)
    if dry_run:
        return f"[DRY-RUN] tag {tag} ({categoria})"
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    return f"tagged {tag} ({categoria})"


def _handle_tag_and_move(graph, user_email, msg_id, rule, dry_run, categoria, parent_folder=None):
    tag = rule.get("tag", TAG_PENDING_MANAGE)
    folder = rule.get("folder", "")
    if dry_run:
        return f"[DRY-RUN] tag {tag} + move -> '{folder}' ({categoria})"
    graph.update_email(user_email, msg_id, is_read=True, add_categories=[tag])
    if folder:
        try:
            graph.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
        except ValueError:
            log.warning(
                "⚠️ Carpeta '%s' no existe para %s — email etiquetado pero no movido",
                folder,
                user_email,
            )
            return f"tagged {tag} (folder '{folder}' not found) ({categoria})"
    return f"tagged {tag} + moved -> '{folder}' ({categoria})"


def _handle_reply_and_move(
    graph,
    user_email,
    msg_id,
    email_msg,
    config,
    classification,
    rule,
    dry_run,
    parent_folder=None,
):
    reply_result = _handle_reply(
        graph, user_email, msg_id, email_msg, config, classification, dry_run
    )
    folder = rule.get("folder", "")
    if dry_run:
        return f"{reply_result} + move -> '{folder}'"
    graph.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
    return f"{reply_result} + moved -> '{folder}'"


# --- Dispatcher ---

_HANDLERS = {
    "forward": lambda ctx: _handle_forward(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["rule"],
        ctx["config"],
        ctx["classification"],
        ctx["dry_run"],
    ),
    "reply": lambda ctx: _handle_reply(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["email_msg"],
        ctx["config"],
        ctx["classification"],
        ctx["dry_run"],
    ),
    "reply_with_attachment": lambda ctx: _handle_reply_with_attachment(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["email_msg"],
        ctx["config"],
        ctx["classification"],
        ctx["rule"],
        ctx["dry_run"],
    ),
    "dynamic_reply": lambda ctx: _handle_dynamic_reply(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["email_msg"],
        ctx["config"],
        ctx["classification"],
        ctx["rule"],
        ctx["openai_client"],
        ctx["body_text"],
        ctx["dry_run"],
    ),
    "silent": lambda ctx: _handle_silent(
        ctx["graph"], ctx["user_email"], ctx["msg_id"], ctx["dry_run"], ctx["categoria"]
    ),
    "delete": lambda ctx: _handle_delete(
        ctx["graph"], ctx["user_email"], ctx["msg_id"], ctx["dry_run"], ctx["categoria"]
    ),
    "move": lambda ctx: _handle_move(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["rule"],
        ctx["dry_run"],
        ctx["categoria"],
        parent_folder=ctx.get("parent_folder"),
    ),
    "tag": lambda ctx: _handle_tag(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["rule"],
        ctx["dry_run"],
        ctx["categoria"],
    ),
    "tag_and_move": lambda ctx: _handle_tag_and_move(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["rule"],
        ctx["dry_run"],
        ctx["categoria"],
        parent_folder=ctx.get("parent_folder"),
    ),
    "reply_and_move": lambda ctx: _handle_reply_and_move(
        ctx["graph"],
        ctx["user_email"],
        ctx["msg_id"],
        ctx["email_msg"],
        ctx["config"],
        ctx["classification"],
        ctx["rule"],
        ctx["dry_run"],
        parent_folder=ctx.get("parent_folder"),
    ),
}


def execute(
    graph,
    config: dict,
    email_msg: dict,
    classification: dict,
    dry_run: bool = False,
    openai_client=None,
    body_text: str = "",
) -> str:
    """Execute the action for a classified email. Returns a short description."""
    user_email = config["email"]
    msg_id = email_msg["id"]
    categoria = classification.get("categoria", "otros")

    routing = config.get("routing", {})
    rule = routing.get(categoria)

    # No routing rule -> fallback to template reply or PENDIENTE GESTIONAR
    if not rule:
        templates = config.get("templates", {})
        if categoria in templates:
            return _handle_reply(
                graph, user_email, msg_id, email_msg, config, classification, dry_run
            )
        log.warning("No routing/template for categoria=%s", categoria)
        return _handle_tag(
            graph, user_email, msg_id, {"tag": TAG_PENDING_MANAGE}, dry_run, categoria
        )

    action = rule.get("action", "tag")
    handler = _HANDLERS.get(action)
    if not handler:
        _subj = email_msg.get("subject", "?")[:80]
        _sender = email_msg.get("from", {}).get("emailAddress", {}).get("address", "?")
        _received = email_msg.get("receivedDateTime", "?")
        log.error(
            "[%s] Acción desconocida '%s' para categoria=%s — De: %s | Asunto: %s | Fecha: %s",
            user_email,
            action,
            categoria,
            _sender,
            _subj,
            _received,
        )
        return _handle_tag(
            graph, user_email, msg_id, {"tag": TAG_PENDING_MANAGE}, dry_run, categoria
        )

    parent_folder = config.get("parent_folder")
    ctx = {
        "graph": graph,
        "user_email": user_email,
        "msg_id": msg_id,
        "email_msg": email_msg,
        "config": config,
        "classification": classification,
        "rule": rule,
        "dry_run": dry_run,
        "categoria": categoria,
        "openai_client": openai_client,
        "body_text": body_text,
        "parent_folder": parent_folder,
    }
    result = handler(ctx)

    # Generic post-action: move to folder if specified and not already handled
    actions_with_move = {"move", "tag_and_move", "reply_and_move"}
    if rule:
        folder = rule.get("folder")
        if folder and action not in actions_with_move:
            if dry_run:
                result += f" + move -> '{folder}'"
            else:
                graph.move_email(user_email, msg_id, folder, parent_folder=parent_folder)
                result += f" + moved -> '{folder}'"

        # Override read status (e.g. is_read: false to keep email unread for manual follow-up)
        if "is_read" in rule:
            if not dry_run:
                graph.update_email(user_email, msg_id, is_read=rule["is_read"])
            if not rule["is_read"]:
                result += " [marked unread]"

    return result
