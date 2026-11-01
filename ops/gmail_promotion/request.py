"""Unprivileged forced SSH command. Receives hashes; never executes a deployment."""

import fcntl
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MAX_REQUESTS = 128
QUEUE = Path("/var/lib/gmail-deploy-requests")
PATTERN = re.compile(r"request ([0-9a-f]{40}) (sha256:[0-9a-f]{64})\Z")


def enqueue(command, queue=QUEUE):
    match = PATTERN.fullmatch(command)
    if not match:
        raise ValueError("Only request FULL_SOURCE_SHA sha256:FULL_DIGEST is allowed")
    if (
        queue.is_symlink()
        or not queue.is_dir()
        or queue.stat().st_uid != os.getuid()
        or queue.stat().st_mode & 0o022
    ):
        raise ValueError("Unsafe request directory")
    source, digest = match.groups()
    identity = hashlib.sha256((source + "\n" + digest).encode()).hexdigest()
    result = {
        "application": "gmail-inbox-bot",
        "request_id": identity,
        "source_sha": source,
        "image_digest": digest,
        "status": "awaiting_admin_approval",
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    lock = os.open(queue / ".lock", flags, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = queue / (identity + ".json")
        if destination.is_symlink():
            raise ValueError("Unsafe request file")
        if destination.exists():
            return result
        if len(list(queue.glob("*.json"))) >= MAX_REQUESTS:
            raise ValueError("Request queue is full; administrator review required")
        fd = os.open(destination, flags | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(result, file, sort_keys=True)
            file.write("\n")
    finally:
        os.close(lock)
    return result


def main():
    if len(sys.argv) != 1:
        raise ValueError("Arguments are not accepted")
    print(json.dumps(enqueue(os.environ.get("SSH_ORIGINAL_COMMAND", ""))))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(64) from None
