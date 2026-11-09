"""Administrator-only approval binds the image digest and frozen runtime data."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .profile import build_profile

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def secure(path, root, owner):
    path = Path(path)
    path.relative_to(root)
    for candidate in [path, *path.parents]:
        if candidate.is_symlink():
            raise ValueError("Symlink in administrator-controlled data")
        info = candidate.stat()
        if info.st_uid != owner or info.st_mode & 0o022:
            raise ValueError("Administrator data has unsafe owner or permissions")
        if candidate == root:
            break
    return path


def config_digest(directory, owner=0):
    records = []
    for file in sorted(directory.iterdir()):
        secure(file, directory, owner)
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", file.name) or not file.is_file():
            raise ValueError("Unexpected config entry")
        if file.stat().st_size > 65536:
            raise ValueError("Config entry too large")
        records.append([file.name, hashlib.sha256(file.read_bytes()).hexdigest()])
    if not 1 <= len(records) <= 32:
        raise ValueError("Invalid config count")
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()


def write_private(path, value):
    fd, temp = tempfile.mkstemp(prefix=".promotion-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, sort_keys=True)
            file.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class Policy:
    def __init__(self, root, owner_uid=0):
        self.root = Path(root)
        self.owner = owner_uid

    def read(self, path):
        secure(path, self.root, self.owner)
        if path.stat().st_size > 1048576:
            raise ValueError("Administrator data too large")
        return json.loads(path.read_text(), object_pairs_hook=unique_object)

    def environment(self):
        path = self.root / "runtime-env.json"
        value = self.read(path)
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and isinstance(v, str)
            for k, v in value.items()
        ):
            raise ValueError("Invalid runtime environment")
        return value, hashlib.sha256(path.read_bytes()).hexdigest()

    def approve(self, source, digest, config):
        if os.geteuid() != self.owner:
            raise ValueError("Only the independent administrator may approve")
        if (
            not HEX40.fullmatch(source)
            or not DIGEST.fullmatch(digest)
            or not HEX64.fullmatch(config)
        ):
            raise ValueError("Approval requires complete hashes")
        directory = self.root / "configs" / config
        secure(directory, self.root, self.owner)
        if config_digest(directory, self.owner) != config:
            raise ValueError("Configuration digest mismatch")
        _, environment_hash = self.environment()
        approval = {
            "application": "gmail-inbox-bot",
            "source_sha": source,
            "image_digest": digest,
            "config_digest": config,
            "environment_digest": environment_hash,
            "decision": "approved",
        }
        identity = hashlib.sha256(json.dumps(approval, sort_keys=True).encode()).hexdigest()
        secure(self.root / "approvals", self.root, self.owner)
        destination = self.root / "approvals" / (identity + ".json")
        if destination.exists() or destination.is_symlink():
            if self.read(destination) != approval:
                raise ValueError("Existing approval differs")
        else:
            write_private(destination, approval)
        return identity

    def apply(self, identity, docker):
        if os.geteuid() != self.owner or not HEX64.fullmatch(identity):
            raise ValueError("Only administrator may apply a full approval ID")
        approval = self.read(self.root / "approvals" / (identity + ".json"))
        if (
            set(approval)
            != {
                "application",
                "source_sha",
                "image_digest",
                "config_digest",
                "environment_digest",
                "decision",
            }
            or approval["application"] != "gmail-inbox-bot"
            or approval["decision"] != "approved"
        ):
            raise ValueError("Invalid approval")
        if hashlib.sha256(json.dumps(approval, sort_keys=True).encode()).hexdigest() != identity:
            raise ValueError("Approval identity mismatch")
        if (
            not HEX40.fullmatch(approval["source_sha"])
            or not DIGEST.fullmatch(approval["image_digest"])
            or not HEX64.fullmatch(approval["config_digest"])
        ):
            raise ValueError("Invalid approval hashes")
        config = self.root / "configs" / approval["config_digest"]
        secure(config, self.root, self.owner)
        environment, environment_hash = self.environment()
        if (
            config_digest(config, self.owner) != approval["config_digest"]
            or environment_hash != approval["environment_digest"]
        ):
            raise ValueError("Approved runtime data changed")
        active = self.root / "active.json"
        if (
            active.exists()
            and self.read(active).get("approval_id") == identity
            and docker.matches(approval)
        ):
            if not docker.healthy():
                raise ValueError("Matching active deployment is unhealthy")
            return {"status": "already_active", "approval_id": identity}
        secure(self.root / "rendered", self.root, self.owner)
        rendered = self.root / "rendered" / (identity + ".json")
        write_private(
            rendered, build_profile(approval["image_digest"], config, environment, identity)
        )
        previous = self.root / "baseline.json"
        if active.exists():
            old = self.read(active)
            if not HEX64.fullmatch(old.get("approval_id", "")):
                raise ValueError("Invalid previous approval")
            previous = self.root / "rendered" / (old["approval_id"] + ".json")
        secure(previous, self.root, self.owner)
        docker.image("ghcr.io/jmgb/gmail-inbox-bot@" + approval["image_digest"])
        try:
            docker.compose(rendered)
            if not docker.healthy():
                raise ValueError("Application did not become healthy")
        except (ValueError, OSError):
            rollback_healthy = False
            try:
                docker.compose(previous)
                rollback_healthy = docker.healthy()
            except (ValueError, OSError):
                pass
            write_private(
                self.root / "last-failure.json",
                {"approval_id": identity, "rollback_healthy": rollback_healthy},
            )
            raise ValueError("Promotion failed; check rollback health") from None
        write_private(active, {"approval_id": identity, **approval})
        return {"status": "promoted", "approval_id": identity}
