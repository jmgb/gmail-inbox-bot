"""Fixed Docker commands, sanitized environment and no shell or repository scripts."""

import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path


class Docker:
    def __init__(self, root):
        self.root = Path(root)
        self.env = {"HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
        self.prefix = [
            "/usr/bin/docker",
            "--host",
            "unix:///var/run/docker.sock",
            "--config",
            str(self.root / "docker"),
        ]

    def run(self, args):
        try:
            result = subprocess.run(
                self.prefix + args,
                env=self.env,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.SubprocessError:
            raise ValueError("Docker operation timed out or failed") from None
        if result.returncode:
            # CLI diagnostics may include expanded environment. Never forward them.
            raise ValueError("Docker operation failed")
        return result.stdout

    def image(self, ref):
        try:
            digests = json.loads(
                self.run(["image", "inspect", "--format", "{{json .RepoDigests}}", ref])
            )
        except ValueError:
            self.run(["pull", ref])
            digests = json.loads(
                self.run(["image", "inspect", "--format", "{{json .RepoDigests}}", ref])
            )
        if ref not in (digests or []):
            raise ValueError("Image digest was not resolved exactly")
        # Other legacy deployers prune untagged images; retain approved rollback material.
        retention = "ghcr.io/jmgb/gmail-inbox-bot:approved-" + ref.rsplit(":", 1)[1]
        self.run(["image", "tag", ref, retention])

    def compose(self, file):
        self.run(
            [
                "compose",
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(self.root),
                "--project-name",
                "gmail-inbox-bot",
                "--file",
                str(file),
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "gmail-inbox-bot",
            ]
        )

    def matches(self, approval):
        fmt = (
            '{"image":{{json .Config.Image}},"labels":{{json .Config.Labels}},'
            '"mounts":{{json .Mounts}},"running":{{json .State.Running}}}'
        )
        try:
            current = json.loads(self.run(["inspect", "--format", fmt, "gmail-inbox-bot"]))
            mount = next(v for v in current["mounts"] if v["Destination"] == "/app/config")
            identity = hashlib.sha256(json.dumps(approval, sort_keys=True).encode()).hexdigest()
            return (
                current["running"]
                and current["image"] == "ghcr.io/jmgb/gmail-inbox-bot@" + approval["image_digest"]
                and (current["labels"] or {}).get("security.gmail.approval") == identity
                and mount["Source"] == str(self.root / "configs" / approval["config_digest"])
                and not mount["RW"]
            )
        except (ValueError, KeyError, StopIteration):
            return False

    def healthy(self):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                state = self.run(
                    ["inspect", "--format", "{{.State.Health.Status}}", "gmail-inbox-bot"]
                ).strip()
                if state == "healthy":
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8007/health", timeout=5
                    ) as response:
                        if response.status == 200:
                            return True
            except (ValueError, OSError):
                pass
            time.sleep(2)
        return False
