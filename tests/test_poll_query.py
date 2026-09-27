"""La query del poll excluye en Gmail los emails ya procesados.

`personal`, `finanzas` y `otros` se quedan sin leer en el inbox con `REVISAR IA` a
propósito, así que `is:unread in:inbox` los vuelve a encontrar en cada ciclo. Antes se
descargaba el mensaje completo (`messages.get?format=full`) para acabar saltándolo en
`already_processed()`: 144 descargas al día por cada email aparcado. Filtrando en la
query, Gmail no los devuelve y `already_processed()` queda como red de seguridad.
"""

from unittest.mock import MagicMock

from gmail_inbox_bot.actions import PROCESSED_TAGS, TAG_REVIEW
from gmail_inbox_bot.bot import process_mailbox
from gmail_inbox_bot.mail_processing import build_poll_query


def test_excluye_todos_los_tags_de_procesado():
    query = build_poll_query("is:unread in:inbox")

    for tag in PROCESSED_TAGS:
        assert f'-label:"{tag}"' in query, f"falta la exclusión de {tag}"


def test_conserva_la_query_base_del_buzon():
    assert build_poll_query("is:unread in:inbox").startswith("is:unread in:inbox")


def test_no_duplica_una_exclusion_ya_escrita_en_el_yaml():
    base = f'is:unread in:inbox -label:"{TAG_REVIEW}"'

    query = build_poll_query(base)

    assert query.count(f'-label:"{TAG_REVIEW}"') == 1


def test_deriva_de_processed_tags_y_no_de_una_lista_copiada(monkeypatch):
    """Si mañana se añade un tag a PROCESSED_TAGS, la query lo recoge sola."""
    monkeypatch.setattr(
        "gmail_inbox_bot.mail_processing.PROCESSED_TAGS", {"TAG NUEVO"}, raising=True
    )

    assert build_poll_query("is:unread") == 'is:unread -label:"TAG NUEVO"'


def test_process_mailbox_pide_a_gmail_la_query_con_exclusiones(config):
    gmail = MagicMock()
    gmail.get_unread_emails.return_value = []

    process_mailbox(gmail, None, config)

    query = gmail.get_unread_emails.call_args.kwargs["query"]
    assert f'-label:"{TAG_REVIEW}"' in query
