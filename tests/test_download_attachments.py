from email.message import EmailMessage
from pathlib import Path

from gmail_inbox_bot.attachment_manifest import Manifest
from scripts.download_attachments import ensure_disk_space, process_message


def _raw_message() -> bytes:
    message = EmailMessage()
    message["From"] = "Ana Example <ana@example.com>"
    message["To"] = "me@example.com"
    message["Subject"] = "Factura piloto á"
    message.set_content("Adjunto la factura.")
    message.add_attachment(
        b"%PDF-1.7",
        maintype="application",
        subtype="pdf",
        filename="factura.pdf",
    )
    message.add_attachment(
        b"\x89PNG\r\n",
        maintype="image",
        subtype="png",
        disposition="inline",
        cid="<logo@example.com>",
    )
    return message.as_bytes()


class FakeGmailClient:
    def get_raw_message(self, message_id: str) -> dict:
        return {
            "id": message_id,
            "threadId": "thread-1",
            "labelIds": ["INBOX"],
            "internalDate": "1784455200000",
            "historyId": "history-1",
            "sizeEstimate": 1234,
            "raw_bytes": _raw_message(),
        }


def test_process_message_archives_eml_extracts_files_and_records_state(tmp_path: Path):
    manifest = Manifest(tmp_path / ".state.sqlite3")

    result = process_message(
        gmail=FakeGmailClient(),
        manifest=manifest,
        output_dir=tmp_path,
        mailbox={"name": "jesus82c", "email": "jesus82c@gmail.com"},
        message_id="message-1",
    )

    assert result == "completed"
    assert list(tmp_path.glob("jesus82c/messages/message-1.eml"))
    attachment_files = list((tmp_path / "jesus82c" / "attachments").iterdir())
    assert len(attachment_files) == 2
    assert all(file.name.startswith("message-1_") for file in attachment_files)
    manifest.export_csv(tmp_path / "messages.csv")
    manifest.export_artifacts_csv(tmp_path / "index.csv")
    assert "Factura piloto á" in (tmp_path / "messages.csv").read_text(encoding="utf-8-sig")
    index = (tmp_path / "index.csv").read_text(encoding="utf-8-sig")
    assert "factura.pdf" in index
    assert "inline_image" in index
    assert "jesus82c/attachments/" in index

    manifest.close()


def test_ensure_disk_space_rejects_low_free_space(tmp_path: Path, monkeypatch):
    class Usage:
        free = 99

    monkeypatch.setattr("scripts.download_attachments.shutil.disk_usage", lambda _: Usage())

    try:
        ensure_disk_space(tmp_path, minimum_free_bytes=100)
    except RuntimeError as exc:
        assert "espacio libre" in str(exc)
    else:
        raise AssertionError("expected low disk space to be rejected")
