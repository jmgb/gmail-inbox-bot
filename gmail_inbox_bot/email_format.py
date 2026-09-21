"""Formato del email compartido por los clasificadores (LLM y Jev).

Módulo sin dependencias internas: ``classifier`` y ``jev_shadow`` lo importan
sin riesgo de ciclos (``mail_processing`` → ``actions`` → ``classifier``).
"""


def format_email_for_classifier(
    *, subject: str, body_text: str, sender_name: str, sender_address: str, has_attachments: bool
) -> str:
    """Texto del email tal y como lo ven los clasificadores (LLM y Jev)."""
    return (
        f"Título del email: {subject}\n\n"
        f"¿Contiene archivo adjunto?: {has_attachments}\n\n"
        f"Remitente: {sender_name} <{sender_address}>\n\n"
        f"Contenido del email:\n{body_text}"
    )
