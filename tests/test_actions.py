"""Tests for actions.py — the config-driven routing engine."""

from datetime import date
from unittest.mock import MagicMock

import gmail_inbox_bot.actions as actions
from gmail_inbox_bot.actions import (
    TAG_DRAFT_FORWARD,
    TAG_DRAFT_REPLY,
    TAG_ERROR,
    TAG_FORWARDED,
    TAG_PENDING_ATTACH,
    TAG_PENDING_MANAGE,
    TAG_REPLIED,
    _plain_to_html,
    already_processed,
    execute,
)
from gmail_inbox_bot.classifier import DEFAULT_MODEL

# ---------- already_processed ----------


class TestAlreadyProcessed:
    def test_no_tags(self, email_msg):
        assert already_processed(email_msg) is False

    def test_respondido_tag(self, email_msg):
        email_msg["categories"] = [TAG_REPLIED]
        assert already_processed(email_msg) is True

    def test_reenviado_tag(self, email_msg):
        email_msg["categories"] = [TAG_FORWARDED]
        assert already_processed(email_msg) is True

    def test_draft_reply_tag(self, email_msg):
        email_msg["categories"] = [TAG_DRAFT_REPLY]
        assert already_processed(email_msg) is True

    def test_draft_forward_tag(self, email_msg):
        email_msg["categories"] = [TAG_DRAFT_FORWARD]
        assert already_processed(email_msg) is True

    def test_pendiente_adjunto_tag(self, email_msg):
        email_msg["categories"] = [TAG_PENDING_ATTACH]
        assert already_processed(email_msg) is True

    def test_pendiente_gestionar_tag(self, email_msg):
        email_msg["categories"] = [TAG_PENDING_MANAGE]
        assert already_processed(email_msg) is True

    def test_error_tag(self, email_msg):
        email_msg["categories"] = [TAG_ERROR]
        assert already_processed(email_msg) is True

    def test_unrelated_tag_not_processed(self, email_msg):
        email_msg["categories"] = ["Urgente", "VIP"]
        assert already_processed(email_msg) is False

    def test_mixed_tags_one_match(self, email_msg):
        email_msg["categories"] = ["Urgente", TAG_REPLIED]
        assert already_processed(email_msg) is True

    def test_empty_categories_key(self):
        assert already_processed({"categories": []}) is False

    def test_missing_categories_key(self):
        assert already_processed({}) is False


# ---------- _plain_to_html ----------


class TestPlainToHtml:
    def test_newlines_to_br(self):
        assert _plain_to_html("Hola\nMundo") == "Hola<br>\nMundo"

    def test_escapes_html(self):
        assert "&amp;" in _plain_to_html("Tom & Jerry")
        assert "&lt;" in _plain_to_html("<script>alert(1)</script>")

    def test_empty_string(self):
        assert _plain_to_html("") == ""


# ---------- execute: forward (via routing) ----------


class TestExecuteForward:
    def test_forward_english(self, graph, config, email_msg):
        classification = {"categoria": "reenvio_ingles", "idioma": "inglés"}

        result = execute(graph, config, email_msg, classification)

        assert "jesus82c@gmail.com" in result
        graph.forward_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            "EN Dest",
            "jesus82c@gmail.com",
            body_prefix="",
        )
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_FORWARDED],
        )

    def test_forward_french(self, graph, config, email_msg):
        classification = {"categoria": "reenvio_frances", "idioma": "francés"}

        result = execute(graph, config, email_msg, classification)

        assert "jesus82c@gmail.com" in result
        graph.forward_email.assert_called_once()

    def test_forward_dry_run(self, graph, config, email_msg):
        classification = {"categoria": "reenvio_ingles", "idioma": "inglés"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "jesus82c@gmail.com" in result
        graph.forward_email.assert_not_called()
        graph.update_email.assert_not_called()

    def test_forward_draft_mode(self, graph, config, email_msg):
        graph.draft_mode = True
        classification = {"categoria": "reenvio_ingles", "idioma": "inglés"}

        result = execute(graph, config, email_msg, classification)

        assert "draft forward" in result
        graph.forward_email.assert_called_once()
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_DRAFT_FORWARD],
        )


# ---------- execute: silent (via routing) ----------


class TestExecuteSilent:
    def test_spam_marks_read(self, graph, config, email_msg):
        classification = {"categoria": "spam", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "silent" in result
        graph.update_email.assert_called_once_with(
            "test@example.com", email_msg["id"], is_read=True
        )
        graph.reply_to_email.assert_not_called()
        graph.forward_email.assert_not_called()

    def test_fuera_oficina_marks_read(self, graph, config, email_msg):
        classification = {"categoria": "fuera_oficina", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "silent" in result
        assert "fuera_oficina" in result

    def test_spam_dry_run(self, graph, config, email_msg):
        classification = {"categoria": "spam", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        graph.update_email.assert_not_called()


# ---------- execute: tag (pending attachment via routing) ----------


class TestExecuteTag:
    def test_informacion_general_tagged(self, graph, config, email_msg):
        classification = {"categoria": "informacion_general", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert TAG_PENDING_ATTACH in result
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_PENDING_ATTACH],
        )
        graph.reply_to_email.assert_not_called()

    def test_all_six_pending_categories(self, graph, config, email_msg):
        pending_cats = [
            "informacion_general",
            "live_sessions_info",
            "fechas_programa",
            "nivel_exigencia",
            "inscripcion_principiantes",
            "inscripcion_avanzadas",
        ]
        for cat in pending_cats:
            graph.reset_mock()
            graph.draft_mode = False
            classification = {"categoria": cat, "idioma": "español"}
            result = execute(graph, config, email_msg, classification)
            assert TAG_PENDING_ATTACH in result, f"Failed for {cat}"

    def test_pending_attachment_dry_run(self, graph, config, email_msg):
        classification = {"categoria": "fechas_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert TAG_PENDING_ATTACH in result
        graph.update_email.assert_not_called()


# ---------- execute: otros (tag via routing) ----------


class TestExecuteOtros:
    def test_otros_tagged_pendiente_gestionar(self, graph, config, email_msg):
        classification = {"categoria": "otros", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert TAG_PENDING_MANAGE in result
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_PENDING_MANAGE],
        )

    def test_otros_dry_run(self, graph, config, email_msg):
        classification = {"categoria": "otros", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        graph.update_email.assert_not_called()


# ---------- execute: auto-reply (template fallback, no routing entry) ----------


class TestExecuteAutoReply:
    def test_reply_esp(self, graph, config, email_msg):
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "esp" in result
        graph.reply_to_email.assert_called_once()
        html_body = graph.reply_to_email.call_args[0][2]
        assert "gratuito" in html_body
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_REPLIED],
        )

    def test_reply_pt_when_portugues(self, graph, config, email_msg):
        classification = {"categoria": "coste_programa", "idioma": "portugués"}

        result = execute(graph, config, email_msg, classification)

        assert "pt" in result
        html_body = graph.reply_to_email.call_args[0][2]
        assert "gratuito" in html_body

    def test_reply_esp_for_non_portugues(self, graph, config, email_msg):
        """Any language other than 'portugués' gets the ESP template."""
        for idioma in ["español", "italiano", "alemán", "otro"]:
            graph.reset_mock()
            graph.draft_mode = False
            classification = {"categoria": "coste_programa", "idioma": idioma}
            result = execute(graph, config, email_msg, classification)
            assert "esp" in result, f"Failed for idioma={idioma}"

    def test_reply_uses_template_variant_until_cutoff_in_madrid(
        self, graph, config, email_msg, monkeypatch
    ):
        config["templates"]["acceso_plataforma"] = {
            "variants": [
                {
                    "valid_until": "2026-03-10",
                    "esp": "TEMPORAL ESP",
                    "pt": "TEMPORAL PT",
                },
                {
                    "valid_from": "2026-03-11",
                    "esp": "ORIGINAL ESP",
                    "pt": "ORIGINAL PT",
                },
            ],
        }
        classification = {"categoria": "acceso_plataforma", "idioma": "español"}
        monkeypatch.setattr(actions, "_today_in_madrid", lambda: date(2026, 3, 10))

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        html_body = graph.reply_to_email.call_args[0][2]
        assert "TEMPORAL ESP" in html_body
        assert "ORIGINAL ESP" not in html_body

    def test_reply_uses_template_variant_from_next_day_in_madrid(
        self, graph, config, email_msg, monkeypatch
    ):
        config["templates"]["acceso_plataforma"] = {
            "variants": [
                {
                    "valid_until": "2026-03-10",
                    "esp": "TEMPORAL ESP",
                    "pt": "TEMPORAL PT",
                },
                {
                    "valid_from": "2026-03-11",
                    "esp": "ORIGINAL ESP",
                    "pt": "ORIGINAL PT",
                },
            ],
        }
        classification = {"categoria": "acceso_plataforma", "idioma": "español"}
        monkeypatch.setattr(actions, "_today_in_madrid", lambda: date(2026, 3, 11))

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        html_body = graph.reply_to_email.call_args[0][2]
        assert "ORIGINAL ESP" in html_body
        assert "TEMPORAL ESP" not in html_body

    def test_reply_adds_re_prefix(self, graph, config, make_email):
        msg = make_email(subject="Consulta sobre coste")
        classification = {"categoria": "coste_programa", "idioma": "español"}

        execute(graph, config, msg, classification)

        subject_arg = graph.reply_to_email.call_args[0][3]
        assert subject_arg.startswith("Re: ")

    def test_reply_no_duplicate_re_prefix(self, graph, config, make_email):
        msg = make_email(subject="Re: Consulta sobre coste")
        classification = {"categoria": "coste_programa", "idioma": "español"}

        execute(graph, config, msg, classification)

        subject_arg = graph.reply_to_email.call_args[0][3]
        assert subject_arg == "Re: Consulta sobre coste"
        assert not subject_arg.startswith("Re: Re:")

    def test_reply_dry_run(self, graph, config, email_msg):
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "coste_programa" in result
        graph.reply_to_email.assert_not_called()
        graph.update_email.assert_not_called()

    def test_reply_draft_mode(self, graph, config, email_msg):
        graph.draft_mode = True
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "draft reply" in result
        assert "esp" in result
        graph.reply_to_email.assert_called_once()
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_DRAFT_REPLY],
        )


# ---------- execute: fallback ----------


class TestExecuteFallback:
    def test_unknown_categoria_fallback_to_pendiente(self, graph, config, email_msg):
        classification = {"categoria": "categoria_inventada", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert TAG_PENDING_MANAGE in result

    def test_missing_categoria_defaults_to_otros(self, graph, config, email_msg):
        classification = {"idioma": "español"}  # no "categoria" key

        result = execute(graph, config, email_msg, classification)

        # defaults to "otros" via .get("categoria", "otros") -> routing hit
        assert TAG_PENDING_MANAGE in result


# ---------- execute: new action types ----------


class TestExecuteDelete:
    def test_delete(self, graph, config, email_msg):
        config["routing"]["borrar_esto"] = {"action": "delete"}
        classification = {"categoria": "borrar_esto", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "deleted" in result
        graph.delete_email.assert_called_once_with("test@example.com", email_msg["id"])

    def test_delete_dry_run(self, graph, config, email_msg):
        config["routing"]["borrar_esto"] = {"action": "delete"}
        classification = {"categoria": "borrar_esto", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "delete" in result
        graph.delete_email.assert_not_called()


class TestExecuteMove:
    def test_move(self, graph, config, email_msg):
        config["routing"]["mover_esto"] = {"action": "move", "folder": "Archivo"}
        classification = {"categoria": "mover_esto", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "moved" in result
        assert "Archivo" in result
        graph.update_email.assert_called_once_with(
            "test@example.com", email_msg["id"], is_read=True
        )
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Archivo", parent_folder=None
        )

    def test_move_dry_run(self, graph, config, email_msg):
        config["routing"]["mover_esto"] = {"action": "move", "folder": "Archivo"}
        classification = {"categoria": "mover_esto", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        graph.move_email.assert_not_called()


class TestExecuteTagAndMove:
    def test_tag_and_move(self, graph, config, email_msg):
        config["routing"]["interno"] = {
            "action": "tag_and_move",
            "tag": "INTERNO",
            "folder": "Internos",
        }
        classification = {"categoria": "interno", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "tagged INTERNO" in result
        assert "Internos" in result
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=["INTERNO"],
        )
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Internos", parent_folder=None
        )


class TestExecuteReplyAndMove:
    def test_reply_and_move(self, graph, config, email_msg):
        config["routing"]["confirmar"] = {
            "action": "reply_and_move",
            "folder": "Confirmados",
        }
        config["templates"]["confirmar"] = {"esp": "Confirmado, gracias."}
        classification = {"categoria": "confirmar", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "Confirmados" in result
        graph.reply_to_email.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Confirmados", parent_folder=None
        )


class TestExecuteReplyWithAttachment:
    def test_reply_with_attachment(self, graph, config, email_msg):
        config["routing"]["titularidad"] = {
            "action": "reply_with_attachment",
            "attachments": [{"path": "files/cert.pdf", "name": "Certificado"}],
        }
        config["templates"]["titularidad"] = {"esp": "Adjunto el certificado."}
        classification = {"categoria": "titularidad", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied+attach" in result
        graph.reply_with_attachment.assert_called_once()
        graph.update_email.assert_called_once_with(
            "test@example.com",
            email_msg["id"],
            is_read=True,
            add_categories=[TAG_REPLIED],
        )

    def test_reply_with_attachment_dry_run(self, graph, config, email_msg):
        config["routing"]["titularidad"] = {
            "action": "reply_with_attachment",
            "attachments": [{"path": "files/cert.pdf", "name": "Certificado"}],
        }
        config["templates"]["titularidad"] = {"esp": "Adjunto el certificado."}
        classification = {"categoria": "titularidad", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "Certificado" in result
        graph.reply_with_attachment.assert_not_called()


class TestExecuteDynamicReply:
    def test_dynamic_reply_dry_run(self, graph, config, email_msg):
        config["routing"]["otros"] = {
            "action": "dynamic_reply",
            "model": DEFAULT_MODEL,
            "response_prompt_file": "prompts/responder.txt",
        }
        classification = {"categoria": "otros", "idioma": "español"}
        client = MagicMock()

        result = execute(
            graph, config, email_msg, classification, dry_run=True, openai_client=client
        )

        assert "[DRY-RUN]" in result
        assert "dynamic_reply" in result

    def test_dynamic_reply_no_client_fallback(self, graph, config, email_msg):
        config["routing"]["otros"] = {
            "action": "dynamic_reply",
            "model": DEFAULT_MODEL,
            "response_prompt_file": "prompts/responder.txt",
        }
        classification = {"categoria": "otros", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, openai_client=None)

        assert TAG_PENDING_MANAGE in result


# ---------- execute: generic folder on any action ----------


class TestGenericFolder:
    def test_reply_with_folder(self, graph, config, email_msg):
        """A reply action with a 'folder' field moves the email after replying."""
        config["routing"]["coste_programa"] = {
            "action": "reply",
            "folder": "Respondidos",
        }
        config["templates"]["coste_programa"] = {"esp": "Es gratuito."}
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "moved" in result
        assert "Respondidos" in result
        graph.reply_to_email.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Respondidos", parent_folder=None
        )

    def test_reply_with_folder_dry_run(self, graph, config, email_msg):
        config["routing"]["coste_programa"] = {
            "action": "reply",
            "folder": "Respondidos",
        }
        config["templates"]["coste_programa"] = {"esp": "Es gratuito."}
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "move" in result
        assert "Respondidos" in result
        graph.move_email.assert_not_called()

    def test_forward_with_folder(self, graph, config, email_msg):
        config["routing"]["redirigir"] = {
            "action": "forward",
            "destination": {"name": "Test", "address": "test@test.com"},
            "folder": "Reenviados",
        }
        classification = {"categoria": "redirigir", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "Reenviados" in result
        graph.forward_email.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Reenviados", parent_folder=None
        )

    def test_reply_with_attachment_and_folder(self, graph, config, email_msg):
        config["routing"]["titularidad"] = {
            "action": "reply_with_attachment",
            "attachments": [{"path": "files/cert.pdf", "name": "Cert"}],
            "folder": "Actualizar Datos",
        }
        config["templates"]["titularidad"] = {"esp": "Adjunto."}
        classification = {"categoria": "titularidad", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied+attach" in result
        assert "moved" in result
        assert "Actualizar Datos" in result
        graph.reply_with_attachment.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Actualizar Datos", parent_folder=None
        )

    def test_reply_and_move_does_not_double_move(self, graph, config, email_msg):
        """reply_and_move already handles folder; generic code should not move again."""
        config["routing"]["confirmar"] = {
            "action": "reply_and_move",
            "folder": "Confirmados",
        }
        config["templates"]["confirmar"] = {"esp": "Confirmado."}
        classification = {"categoria": "confirmar", "idioma": "español"}

        execute(graph, config, email_msg, classification)

        # Only one move call (from reply_and_move handler)
        graph.move_email.assert_called_once()

    def test_no_folder_no_move(self, graph, config, email_msg):
        """Without a 'folder' field, no move happens."""
        classification = {"categoria": "coste_programa", "idioma": "español"}

        execute(graph, config, email_msg, classification)

        graph.move_email.assert_not_called()


# ---------- execute: generic is_read override ----------


class TestGenericIsRead:
    def test_reply_with_is_read_false(self, graph, config, email_msg):
        """is_read: false overrides default behavior, marking email unread."""
        config["routing"]["bajas"] = {"action": "reply", "is_read": False}
        config["templates"]["bajas"] = {"esp": "Trasladamos tu solicitud."}
        classification = {"categoria": "bajas", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "marked unread" in result
        # Reply handler marks is_read=True, then generic override sets is_read=False
        calls = graph.update_email.call_args_list
        assert len(calls) == 2
        assert calls[1][1]["is_read"] is False

    def test_reply_with_is_read_false_dry_run(self, graph, config, email_msg):
        config["routing"]["bajas"] = {"action": "reply", "is_read": False}
        config["templates"]["bajas"] = {"esp": "Trasladamos tu solicitud."}
        classification = {"categoria": "bajas", "idioma": "español"}

        result = execute(graph, config, email_msg, classification, dry_run=True)

        assert "[DRY-RUN]" in result
        assert "marked unread" in result
        graph.update_email.assert_not_called()

    def test_reply_with_folder_and_is_read_false(self, graph, config, email_msg):
        """Combines folder move + is_read override (Contabilidad bajas case)."""
        config["routing"]["bajas"] = {
            "action": "reply",
            "folder": "Bajas",
            "is_read": False,
        }
        config["templates"]["bajas"] = {"esp": "Trasladamos tu solicitud."}
        classification = {"categoria": "bajas", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "moved" in result
        assert "Bajas" in result
        assert "marked unread" in result
        graph.reply_to_email.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", email_msg["id"], "Bajas", parent_folder=None
        )

    def test_is_read_true_does_not_append_marker(self, graph, config, email_msg):
        """is_read: true should not add '[marked unread]' marker."""
        config["routing"]["test"] = {"action": "reply", "is_read": True}
        config["templates"]["test"] = {"esp": "Ok."}
        classification = {"categoria": "test", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "marked unread" not in result


# ---------- Forwarded email support ----------


class TestForwardedEmailReply:
    """Tests for forwarded email detection: override_to + force_draft fallback."""

    def test_reply_with_original_sender_override(self, graph, config, email_msg):
        """When _original_sender is set, reply goes to the original sender."""
        email_msg["_original_sender"] = {
            "name": "Original Person",
            "address": "original@external.com",
        }
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "original@external.com" in result
        graph.reply_to_email.assert_called_once()
        call_kwargs = graph.reply_to_email.call_args
        assert call_kwargs[1]["override_to"] == {
            "name": "Original Person",
            "address": "original@external.com",
        }
        assert call_kwargs[1]["force_draft"] is False

    def test_reply_forward_extraction_failed_creates_draft(self, graph, config, email_msg):
        """When extraction fails, force draft with warning banner."""
        email_msg["_forward_extraction_failed"] = True
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "forward fallback" in result
        graph.reply_to_email.assert_called_once()
        call_kwargs = graph.reply_to_email.call_args
        assert call_kwargs[1]["force_draft"] is True
        assert call_kwargs[1]["override_to"] is None
        # HTML body should contain the warning banner
        html_body = call_kwargs[0][2]  # 3rd positional arg
        assert "VERIFICAR DESTINATARIO" in html_body
        # Tag should be DRAFT even if graph is not in draft mode
        graph.update_email.assert_called_once()
        cats = graph.update_email.call_args[1]["add_categories"]
        assert TAG_DRAFT_REPLY in cats

    def test_reply_with_attachment_override(self, graph, config, email_msg):
        """reply_with_attachment also respects _original_sender."""
        email_msg["_original_sender"] = {
            "name": "External",
            "address": "ext@test.com",
        }
        config["routing"]["titularidad"] = {
            "action": "reply_with_attachment",
            "attachments": [{"path": "files/cert.pdf", "name": "Cert"}],
        }
        config["templates"]["titularidad"] = {"esp": "Adjunto certificado."}
        classification = {"categoria": "titularidad", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "ext@test.com" in result
        graph.reply_with_attachment.assert_called_once()
        call_kwargs = graph.reply_with_attachment.call_args
        assert call_kwargs[1]["override_to"]["address"] == "ext@test.com"

    def test_normal_email_no_override(self, graph, config, email_msg):
        """Normal emails (no _original_sender, no _forward_extraction_failed) are unaffected."""
        classification = {"categoria": "coste_programa", "idioma": "español"}

        result = execute(graph, config, email_msg, classification)

        assert "replied" in result
        assert "forward" not in result
        graph.reply_to_email.assert_called_once()
        call_kwargs = graph.reply_to_email.call_args
        assert call_kwargs[1]["override_to"] is None
        assert call_kwargs[1]["force_draft"] is False
