import csv
import sys
from pathlib import Path

import pytest

from gmail_inbox_bot.attachment_manifest import Manifest
from scripts import trash_marked
from scripts.download_attachments import process_message
from scripts.trash_marked import ValidationError, confirm_trash, validate_marked_messages


class FakeGmail:
    def get_raw_message(self, message_id: str) -> dict:
        return {
            "id": message_id,
            "threadId": "thread-1",
            "labelIds": ["INBOX"],
            "internalDate": "1784455200000",
            "sizeEstimate": 123,
            "raw_bytes": (
                b"From: sender@example.com\r\n"
                b"To: me@example.com\r\n"
                b"Subject: Test\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"\r\nhello\r\n"
            ),
        }


def _archive(tmp_path: Path) -> Path:
    manifest = Manifest(tmp_path / ".state.sqlite3")
    process_message(
        gmail=FakeGmail(),
        manifest=manifest,
        output_dir=tmp_path,
        mailbox={"name": "jesus82c", "email": "jesus82c@gmail.com"},
        message_id="message-1",
    )
    manifest.export_csv(tmp_path / "messages.csv")
    manifest.export_artifacts_csv(tmp_path / "index.csv")
    manifest.close()
    rows = list(csv.DictReader((tmp_path / "messages.csv").open(encoding="utf-8-sig")))
    rows[0]["borrar"] = "x"
    with (tmp_path / "messages.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path / "messages.csv"


def test_validate_marked_messages_checks_manifest_and_hashes(tmp_path: Path):
    messages_csv = _archive(tmp_path)

    marked = validate_marked_messages(messages_csv)

    assert len(marked) == 1
    assert marked[0]["message_id"] == "message-1"
    assert marked[0]["artifacts_count"] == 0


def test_validate_marked_messages_blocks_corrupt_eml(tmp_path: Path):
    messages_csv = _archive(tmp_path)
    row = next(csv.DictReader(messages_csv.open(encoding="utf-8-sig")))
    (tmp_path / row["ruta_eml"]).write_bytes(b"tampered")

    with pytest.raises(ValidationError, match="inconsistente"):
        validate_marked_messages(messages_csv)


def test_validate_marked_messages_rejects_invalid_marker(tmp_path: Path):
    messages_csv = _archive(tmp_path)
    rows = list(csv.DictReader(messages_csv.open(encoding="utf-8-sig")))
    rows[0]["borrar"] = "si"
    with messages_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValidationError, match="borrar"):
        validate_marked_messages(messages_csv)


def test_validate_marked_messages_blocks_conflicting_keep_and_delete_markers(tmp_path: Path):
    messages_csv = _archive(tmp_path)
    rows = list(csv.DictReader(messages_csv.open(encoding="utf-8-sig")))
    rows[0]["conservar"] = "x"
    with messages_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValidationError, match="conservar y borrar"):
        validate_marked_messages(messages_csv)


def test_confirmation_requires_exact_count_phrase():
    assert confirm_trash(2, input_fn=lambda _: "TRASH 2") is True
    assert confirm_trash(2, input_fn=lambda _: " trash 2 ") is False


def test_dry_run_never_builds_gmail_client(tmp_path: Path, monkeypatch, capsys):
    messages_csv = _archive(tmp_path)
    monkeypatch.setattr(
        trash_marked,
        "_build_gmail_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gmail call")),
    )
    monkeypatch.setattr(sys, "argv", ["trash_marked.py", "--messages", str(messages_csv)])

    assert trash_marked.main() == 0
    assert "mensajes marcados=1" in capsys.readouterr().out
