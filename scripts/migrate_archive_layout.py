"""Migrate the first pilot archive to the flat manual-review layout."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from gmail_inbox_bot.attachment_archive import safe_filename
from gmail_inbox_bot.attachment_manifest import Manifest


def _copy_verified(source: Path, target: Path, expected_sha256: str) -> None:
    if not source.is_file() and target.is_file():
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual == expected_sha256:
            return
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha256:
        return
    temporary = target.with_name(f".{target.name}.part")
    shutil.copy2(source, temporary)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"hash mismatch while migrating {source}")
    os.replace(temporary, target)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def migrate_archive(output_dir: Path, manifest: Manifest) -> None:
    """Copy/verify legacy files into flat folders and update SQLite paths."""
    messages = manifest.db.execute("SELECT * FROM messages").fetchall()
    artifacts = manifest.db.execute("SELECT * FROM artifacts").fetchall()
    moves: list[tuple[Path, Path]] = []

    for row in messages:
        source = output_dir / row["eml_path"]
        target = (
            output_dir
            / safe_filename(row["mailbox"])
            / "messages"
            / (f"{safe_filename(row['message_id'])}.eml")
        )
        _copy_verified(source, target, row["eml_sha256"])
        manifest.db.execute(
            "UPDATE messages SET eml_path = ? WHERE account = ? AND message_id = ?",
            (str(target.relative_to(output_dir)), row["account"], row["message_id"]),
        )
        if source != target:
            moves.append((source, target))

    for row in artifacts:
        source = output_dir / row["local_path"]
        target_name = (
            f"{safe_filename(row['message_id'])}_{safe_filename(row['part_key'])}_"
            f"{safe_filename(row['filename'])}"
        )
        target = (
            output_dir / safe_filename(row["account"].split("@")[0]) / "attachments" / target_name
        )
        _copy_verified(source, target, row["sha256"])
        manifest.db.execute(
            """
            UPDATE artifacts SET local_path = ?
            WHERE account = ? AND message_id = ? AND part_key = ?
            """,
            (
                str(target.relative_to(output_dir)),
                row["account"],
                row["message_id"],
                row["part_key"],
            ),
        )
        if source != target:
            moves.append((source, target))

    manifest.db.commit()
    for source, target in moves:
        if _inside(output_dir, source) and source.exists() and source != target:
            source.unlink()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Migrate a Gmail archive to flat folders")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = Manifest(args.output_dir / ".state.sqlite3")
    try:
        migrate_archive(args.output_dir, manifest)
        manifest.export_csv(
            args.output_dir / "messages.csv", existing_csv=args.output_dir / "messages.csv"
        )
        manifest.export_artifacts_csv(args.output_dir / "index.csv")
    finally:
        manifest.close()
    print("archive_layout=migrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
