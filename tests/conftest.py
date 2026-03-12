"""Shared fixtures for gmail_inbox_bot tests."""

import copy

import pytest

MOCK_CONFIG = {
    "name": "TestMailbox",
    "email": "test@example.com",
    "classifier": {
        "prompt_file": "gmail_inbox_bot/prompts/proveedores_sostenibles.txt",
    },
    "max_emails_per_poll": 50,
    "poll_interval_seconds": 120,
    "routing": {
        "reenvio_ingles": {
            "action": "forward",
            "destination": {"name": "EN Dest", "address": "jesus82c@gmail.com"},
        },
        "reenvio_frances": {
            "action": "forward",
            "destination": {"name": "FR Dest", "address": "jesus82c@gmail.com"},
        },
        "spam": {"action": "silent"},
        "fuera_oficina": {"action": "silent"},
        "informacion_general": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "live_sessions_info": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "fechas_programa": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "nivel_exigencia": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "inscripcion_principiantes": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "inscripcion_avanzadas": {"action": "tag", "tag": "PENDIENTE ADJUNTO"},
        "otros": {"action": "tag", "tag": "PENDIENTE GESTIONAR"},
    },
    "templates": {
        "coste_programa": {
            "esp": "Hola,\n\nEl programa es 100% gratuito.\n\nUn saludo",
            "pt": "Olá,\n\nO programa é 100% gratuito.\n\nAtenciosamente,",
        },
        "cambio_contrasena": {
            "esp": "Hola,\n\nResetea tu contraseña aquí.\n\nUn saludo",
            "pt": "Olá,\n\nRedefina a sua senha aqui.\n\nAtenciosamente,",
        },
    },
}


def _make_email(
    msg_id="AAMkAGQ0MjBmNWIx",
    subject="Pregunta sobre el coste",
    sender_name="Juan García",
    sender_address="juan@empresa.com",
    body_html="<html><body><p>Hola, ¿el programa es gratuito?</p></body></html>",
    has_attachments=False,
    categories=None,
):
    return {
        "id": msg_id,
        "subject": subject,
        "from": {"emailAddress": {"name": sender_name, "address": sender_address}},
        "sender": {"emailAddress": {"name": sender_name, "address": sender_address}},
        "body": {"content": body_html},
        "hasAttachments": has_attachments,
        "categories": categories or [],
        "receivedDateTime": "2026-02-20T10:00:00Z",
    }


@pytest.fixture
def config():
    return copy.deepcopy(MOCK_CONFIG)


@pytest.fixture
def email_msg():
    return _make_email()


@pytest.fixture
def make_email():
    """Factory fixture — call with overrides to get a custom email dict."""

    def _factory(**kwargs):
        return _make_email(**kwargs)

    return _factory


@pytest.fixture
def graph():
    """Mock mail client with draft_mode=False by default."""
    from unittest.mock import MagicMock

    g = MagicMock()
    g.draft_mode = False
    return g
