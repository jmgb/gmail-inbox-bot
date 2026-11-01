"""Root-only entry point. Installed code and data paths are fixed outside GitHub."""

import fcntl
import json
import os
import sys
from pathlib import Path

ROOT = Path("/opt/gmail-promotion")


def main():
    if os.geteuid() != 0:
        raise ValueError("Independent administrator required")
    package = Path(__file__).resolve().parent
    for path in [package, *package.parents]:
        if path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ValueError("Installed code has unsafe permissions")
    for path in package.glob("*.py"):
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ValueError("Installed module has unsafe permissions")
    # -I excludes the script path; add only the verified administrator-owned parent.
    sys.path.insert(0, str(package.parent))
    from gmail_promotion.docker_runtime import Docker
    from gmail_promotion.promotion import Policy, secure

    secure(ROOT, ROOT, 0)
    for parent in ROOT.parents:
        if parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
            raise ValueError("Unsafe promotion parent")
    secure(ROOT / "docker" / "config.json", ROOT, 0)
    config = json.loads((ROOT / "docker" / "config.json").read_text())
    if set(config) != {"auths"} or set(config["auths"]) - {"ghcr.io"}:
        raise ValueError("Docker helpers or extra registries are forbidden")
    args = sys.argv[1:]
    policy = Policy(ROOT)
    fd = os.open(ROOT / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if len(args) == 4 and args[0] == "approve":
            result = {"approval_id": policy.approve(*args[1:]), "status": "approved_not_deployed"}
        elif len(args) == 2 and args[0] == "apply":
            result = policy.apply(args[1], Docker(ROOT))
        else:
            raise ValueError(
                "Usage: approve SOURCE_SHA IMAGE_DIGEST CONFIG_DIGEST | apply APPROVAL_ID"
            )
    finally:
        os.close(fd)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print(
            "Promotion rejected or failed; inspect administrator state and service health.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
