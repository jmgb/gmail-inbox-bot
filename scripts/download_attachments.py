"""Run a bounded, resumable Gmail archive download."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

from gmail_inbox_bot.attachment_archive import extract_artifacts, safe_filename
from gmail_inbox_bot.attachment_manifest import Manifest
from gmail_inbox_bot.bot import _build_gmail_client
from gmail_inbox_bot.config import load_env, load_mailbox_configs


def _iso_date(internal_date: str) -> str:
    try:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _atomic_bytes(path: Path, data: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    if path.exists() and path.stat().st_size == len(data):
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current == digest:
            return len(data), digest
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_bytes(data)
    with temporary.open("rb") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return len(data), digest


def process_message(
    *,
    gmail,
    manifest: Manifest,
    output_dir: Path,
    mailbox: dict,
    message_id: str,
) -> str:
    """Archive one raw Gmail message and record its extracted artifacts."""
    raw = gmail.get_raw_message(message_id)
    raw_bytes = raw["raw_bytes"]
    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    account = mailbox["email"]
    mailbox_name = mailbox.get("name", account)
    internal_date = _iso_date(raw.get("internalDate", ""))
    mailbox_dir = output_dir / safe_filename(mailbox_name)
    eml_path = mailbox_dir / "messages" / f"{safe_filename(message_id)}.eml"
    eml_size, eml_sha256 = _atomic_bytes(eml_path, raw_bytes)
    sender = parseaddr(parsed.get("From", ""))[1] or parsed.get("From", "")

    manifest.record_message(
        account=account,
        mailbox=mailbox_name,
        message_id=message_id,
        thread_id=raw.get("threadId", ""),
        subject=parsed.get("Subject", ""),
        sender=sender,
        internal_date=internal_date,
        label_ids=raw.get("labelIds", []),
        gmail_size_estimate=raw.get("sizeEstimate", 0),
        eml_path=str(eml_path.relative_to(output_dir)),
        eml_size=eml_size,
        eml_sha256=eml_sha256,
        status="processing",
    )

    artifacts = extract_artifacts(
        raw_bytes,
        mailbox_dir / "attachments",
        filename_prefix=f"{safe_filename(message_id)}_",
    )
    for artifact in artifacts:
        manifest.record_artifact(
            account=account,
            message_id=message_id,
            part_key=artifact.part_key,
            kind=artifact.kind,
            filename=artifact.filename,
            disposition=artifact.disposition,
            content_id=artifact.content_id,
            local_path=str(artifact.path.relative_to(output_dir)),
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            status="completed",
        )
    manifest.record_message(
        account=account,
        mailbox=mailbox_name,
        message_id=message_id,
        thread_id=raw.get("threadId", ""),
        subject=parsed.get("Subject", ""),
        sender=sender,
        internal_date=internal_date,
        label_ids=raw.get("labelIds", []),
        gmail_size_estimate=raw.get("sizeEstimate", 0),
        eml_path=str(eml_path.relative_to(output_dir)),
        eml_size=eml_size,
        eml_sha256=eml_sha256,
        status="completed",
    )
    return "completed"


def _select_mailbox(configs: list[dict], selector: str) -> dict:
    for config in configs:
        if selector in {config.get("name"), config.get("email")}:
            return config
    raise ValueError(f"mailbox not found in config/: {selector}")


def run_pilot(
    *,
    output_dir: Path,
    mailbox_selector: str,
    query: str | None,
    include_spam_trash: bool,
    max_messages: int | None,
    workers: int,
) -> dict[str, int]:
    if workers != 1:
        raise ValueError("the initial pilot supports --workers 1; parallel workers come later")
    if max_messages is not None and max_messages < 1:
        raise ValueError("max_messages must be positive")

    env = load_env()
    mailbox = _select_mailbox(load_mailbox_configs(), mailbox_selector)
    gmail = _build_gmail_client(env, mailbox)
    manifest = Manifest(output_dir / ".state.sqlite3")
    completed_ids = manifest.pending_message_ids(mailbox["email"])
    processed = 0
    skipped = 0
    errors = 0
    try:
        for stub in gmail.iter_message_stubs(
            query=query,
            include_spam_trash=include_spam_trash,
            page_size=500,
        ):
            message_id = stub["id"]
            if message_id in completed_ids:
                skipped += 1
                continue
            if max_messages is not None and processed >= max_messages:
                break
            try:
                process_message(
                    gmail=gmail,
                    manifest=manifest,
                    output_dir=output_dir,
                    mailbox=mailbox,
                    message_id=message_id,
                )
                processed += 1
            except Exception as exc:  # noqa: BLE001 - one bad email must not stop the pilot
                errors += 1
                print(
                    f"error en mensaje {message_id}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        manifest.export_csv(output_dir / "messages.csv", existing_csv=output_dir / "messages.csv")
        manifest.export_artifacts_csv(output_dir / "index.csv")
    finally:
        manifest.close()
        gmail._http.close()
    return {"processed": processed, "skipped": skipped, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive a bounded Gmail message set locally")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mailbox", required=True, help="Mailbox name or email")
    parser.add_argument("--query", default="has:attachment")
    parser.add_argument("--all-messages", action="store_true")
    parser.add_argument("--include-spam-trash", action="store_true")
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.all_messages and args.query != "has:attachment":
        parser.error("use --all-messages without --query")
    query = None if args.all_messages else args.query
    try:
        summary = run_pilot(
            output_dir=args.output_dir,
            mailbox_selector=args.mailbox,
            query=query,
            include_spam_trash=args.include_spam_trash,
            max_messages=args.max_messages,
            workers=args.workers,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"configuración inválida: {exc}", file=sys.stderr)
        return 2
    print(
        f"completados={summary['processed']} omitidos={summary['skipped']} "
        f"errores={summary['errors']}"
    )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
