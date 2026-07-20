"""Durable manifest and CSV projections for the Gmail archive pilot."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from email.header import decode_header, make_header
from pathlib import Path

MESSAGE_FIELDS = [
    "cuenta",
    "mailbox",
    "thread_id",
    "message_id",
    "gmail_url",
    "fecha",
    "de",
    "asunto",
    "labels",
    "tamano_gmail_estimado",
    "numero_ficheros",
    "tamano_ficheros",
    "ruta_eml",
    "sha256_eml",
    "estado_archivo",
    "error",
    "borrar",
    "conservar",
]
ARTIFACT_FIELDS = [
    "cuenta",
    "message_id",
    "part_key",
    "gmail_url",
    "fecha",
    "de",
    "asunto",
    "tipo",
    "disposition",
    "content_id",
    "nombre_fichero",
    "ruta_local",
    "tamano_bytes",
    "mime_type",
    "sha256",
    "estado",
    "error",
]


def _csv_safe(value: object) -> str | int:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return value if isinstance(value, int) else text


def _decode_header_value(value: object) -> str:
    text = str(value or "")
    try:
        return str(make_header(decode_header(text)))
    except (LookupError, UnicodeDecodeError, ValueError):
        return text


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                account TEXT NOT NULL,
                mailbox TEXT NOT NULL,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                sender TEXT NOT NULL,
                internal_date TEXT NOT NULL,
                label_ids TEXT NOT NULL,
                gmail_size_estimate INTEGER NOT NULL DEFAULT 0,
                eml_path TEXT NOT NULL,
                eml_size INTEGER NOT NULL DEFAULT 0,
                eml_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (account, message_id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                account TEXT NOT NULL,
                message_id TEXT NOT NULL,
                part_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL,
                disposition TEXT NOT NULL DEFAULT '',
                content_id TEXT NOT NULL DEFAULT '',
                local_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (account, message_id, part_key)
            );
            """
        )
        self.db.commit()

    def record_message(self, **message: object) -> None:
        self.db.execute(
            """
            INSERT INTO messages (
                account, mailbox, message_id, thread_id, subject, sender,
                internal_date, label_ids, gmail_size_estimate, eml_path,
                eml_size, eml_sha256, status, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account, message_id) DO UPDATE SET
                mailbox=excluded.mailbox, thread_id=excluded.thread_id,
                subject=excluded.subject, sender=excluded.sender,
                internal_date=excluded.internal_date, label_ids=excluded.label_ids,
                gmail_size_estimate=excluded.gmail_size_estimate, eml_path=excluded.eml_path,
                eml_size=excluded.eml_size, eml_sha256=excluded.eml_sha256,
                status=excluded.status, last_error=excluded.last_error
            """,
            (
                message["account"],
                message["mailbox"],
                message["message_id"],
                message.get("thread_id", ""),
                message.get("subject", ""),
                message.get("sender", ""),
                message.get("internal_date", ""),
                json.dumps(message.get("label_ids", []), ensure_ascii=False),
                message.get("gmail_size_estimate", 0),
                message.get("eml_path", ""),
                message.get("eml_size", 0),
                message.get("eml_sha256", ""),
                message.get("status", "discovered"),
                message.get("last_error", ""),
            ),
        )
        self.db.commit()

    def record_artifact(self, **artifact: object) -> None:
        self.db.execute(
            """
            INSERT INTO artifacts (
                account, message_id, part_key, kind, filename, disposition, content_id,
                local_path, mime_type, size_bytes, sha256, status, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account, message_id, part_key) DO UPDATE SET
                kind=excluded.kind, filename=excluded.filename, disposition=excluded.disposition,
                content_id=excluded.content_id, local_path=excluded.local_path,
                mime_type=excluded.mime_type, size_bytes=excluded.size_bytes,
                sha256=excluded.sha256, status=excluded.status, last_error=excluded.last_error
            """,
            (
                artifact["account"],
                artifact["message_id"],
                artifact["part_key"],
                artifact["kind"],
                artifact.get("filename", ""),
                artifact.get("disposition", ""),
                artifact.get("content_id", ""),
                artifact.get("local_path", ""),
                artifact.get("mime_type", ""),
                artifact.get("size_bytes", 0),
                artifact.get("sha256", ""),
                artifact.get("status", "completed"),
                artifact.get("last_error", ""),
            ),
        )
        self.db.commit()

    def pending_message_ids(self, account: str) -> set[str]:
        rows = self.db.execute(
            "SELECT message_id FROM messages WHERE account = ? AND status = 'completed'", (account,)
        )
        return {row["message_id"] for row in rows}

    def export_csv(self, path: Path, existing_csv: Path | None = None) -> None:
        markers: dict[tuple[str, str], tuple[str, str]] = {}
        if existing_csv and existing_csv.exists():
            with existing_csv.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    borrar = row.get("borrar", "").strip().lower()
                    conservar = row.get("conservar", "").strip().lower()
                    if borrar not in {"", "x"}:
                        raise ValueError(f"invalid borrar marker for {row.get('message_id', '?')}")
                    if conservar not in {"", "x"}:
                        raise ValueError(
                            f"invalid conservar marker for {row.get('message_id', '?')}"
                        )
                    if borrar == "x" and conservar == "x":
                        raise ValueError(
                            f"message cannot be both borrar and conservar: "
                            f"{row.get('message_id', '?')}"
                        )
                    key = (row.get("cuenta", ""), row.get("message_id", ""))
                    if key in markers:
                        raise ValueError(f"duplicate message in CSV: {key}")
                    markers[key] = (borrar, conservar)

        rows = self.db.execute("SELECT * FROM messages ORDER BY account, internal_date, message_id")
        output_rows: list[dict[str, object]] = []
        for row in rows:
            artifacts = self.db.execute(
                """
                SELECT size_bytes FROM artifacts
                WHERE account = ? AND message_id = ? AND status = 'completed'
                """,
                (row["account"], row["message_id"]),
            ).fetchall()
            key = (row["account"], row["message_id"])
            output_rows.append(
                {
                    "cuenta": row["account"],
                    "mailbox": row["mailbox"],
                    "thread_id": row["thread_id"],
                    "message_id": row["message_id"],
                    "gmail_url": f"https://mail.google.com/mail/u/{row['account']}/#all/{row['thread_id']}",
                    "fecha": row["internal_date"],
                    "de": _csv_safe(_decode_header_value(row["sender"])),
                    "asunto": _csv_safe(_decode_header_value(row["subject"])),
                    "labels": row["label_ids"],
                    "tamano_gmail_estimado": row["gmail_size_estimate"],
                    "numero_ficheros": len(artifacts),
                    "tamano_ficheros": sum(item["size_bytes"] for item in artifacts),
                    "ruta_eml": row["eml_path"],
                    "sha256_eml": row["eml_sha256"],
                    "estado_archivo": row["status"],
                    "error": _csv_safe(row["last_error"]),
                    "borrar": markers.get(key, ("", ""))[0],
                    "conservar": markers.get(key, ("", ""))[1],
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.part")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MESSAGE_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(output_rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def export_artifacts_csv(self, path: Path) -> None:
        rows = self.db.execute(
            """
            SELECT a.*, m.account, m.thread_id, m.internal_date, m.sender, m.subject
            FROM artifacts AS a
            JOIN messages AS m ON m.account = a.account AND m.message_id = a.message_id
            ORDER BY a.account, m.internal_date, a.message_id, a.part_key
            """
        )
        output_rows: list[dict[str, object]] = []
        for row in rows:
            output_rows.append(
                {
                    "cuenta": row["account"],
                    "message_id": row["message_id"],
                    "part_key": row["part_key"],
                    "gmail_url": f"https://mail.google.com/mail/u/{row['account']}/#all/{row['thread_id']}",
                    "fecha": row["internal_date"],
                    "de": _csv_safe(_decode_header_value(row["sender"])),
                    "asunto": _csv_safe(_decode_header_value(row["subject"])),
                    "tipo": row["kind"],
                    "disposition": row["disposition"],
                    "content_id": row["content_id"],
                    "nombre_fichero": _csv_safe(row["filename"]),
                    "ruta_local": row["local_path"],
                    "tamano_bytes": row["size_bytes"],
                    "mime_type": row["mime_type"],
                    "sha256": row["sha256"],
                    "estado": row["status"],
                    "error": _csv_safe(row["last_error"]),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.part")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ARTIFACT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(output_rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def close(self) -> None:
        self.db.close()
