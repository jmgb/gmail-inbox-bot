"""Security boundary: CI submits data; only an independent administrator promotes."""

import hashlib
import importlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    receiver = importlib.import_module("ops.gmail_promotion.request")
    promotion = importlib.import_module("ops.gmail_promotion.promotion")
except ModuleNotFoundError:
    receiver = promotion = None

SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64


class RequestBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(receiver, "Missing isolated request receiver")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Path(self.temp.name)

    def test_valid_request_only_creates_bounded_data(self):
        result = receiver.enqueue(f"request {SOURCE} {DIGEST}", self.queue)
        self.assertEqual(result["status"], "awaiting_admin_approval")
        files = list(self.queue.glob("*.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text())
        self.assertEqual(data["source_sha"], SOURCE)
        self.assertEqual(data["image_digest"], DIGEST)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_shell_and_arbitrary_commands_are_rejected(self):
        for command in [
            "id",
            "sudo id",
            "cat /etc/shadow",
            "sftp",
            f"request {SOURCE} {DIGEST}; id",
            f"request $(id) {DIGEST}",
        ]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                receiver.enqueue(command, self.queue)
        self.assertFalse(list(self.queue.glob("*.json")))

    def test_tags_other_registries_and_partial_hashes_are_rejected(self):
        for digest in ["latest", "sha256:123", f"ghcr.io/evil/other@{DIGEST}"]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                receiver.enqueue(f"request {SOURCE} {digest}", self.queue)

    def test_duplicate_request_does_not_overwrite(self):
        first = receiver.enqueue(f"request {SOURCE} {DIGEST}", self.queue)
        path = self.queue / (first["request_id"] + ".json")
        before = path.stat().st_mtime_ns
        second = receiver.enqueue(f"request {SOURCE} {DIGEST}", self.queue)
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_symlink_queue_and_queue_limit_rejected(self):
        outside = self.queue / "outside"
        outside.mkdir()
        link = self.queue / "link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            receiver.enqueue(f"request {SOURCE} {DIGEST}", link)
        for n in range(receiver.MAX_REQUESTS):
            (outside / f"{n:064x}.json").write_text("{}")
        with self.assertRaises(ValueError):
            receiver.enqueue(f"request {SOURCE} {DIGEST}", outside)

    def test_world_writable_queue_rejected(self):
        self.queue.chmod(0o777)
        with self.assertRaises(ValueError):
            receiver.enqueue(f"request {SOURCE} {DIGEST}", self.queue)


class RecordingDocker:
    """Docker boundary substitute; test real approval files and state transitions."""

    def __init__(self, fail_health=False):
        self.commands = []
        self.fail_health = fail_health
        self.matches_active = False

    def image(self, ref):
        self.commands.append(("image", ref))

    def compose(self, file):
        self.commands.append(("compose", str(file)))
        self.matches_active = True

    def matches(self, approval):
        return self.matches_active

    def healthy(self):
        self.commands.append(("health",))
        return not self.fail_health


class PromotionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(promotion, "Missing administrator promotion gate")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        for name in ["configs", "approvals", "rendered"]:
            (self.root / name).mkdir(mode=0o700)
        self.config = self.root / "configs" / ("c" * 64)
        self.config.mkdir(mode=0o700)
        (self.config / "mailbox.yml").write_text("enabled: true\n")
        (self.config / "mailbox.yml").chmod(0o600)
        self.config_id = promotion.config_digest(self.config, os.getuid())
        self.config.rename(self.root / "configs" / self.config_id)
        self.config = self.root / "configs" / self.config_id
        (self.root / "runtime-env.json").write_text(
            json.dumps({"TEST_VALUE": "literal${HOME}$value"})
        )
        (self.root / "runtime-env.json").chmod(0o600)
        (self.root / "baseline.json").write_text("{}")
        (self.root / "baseline.json").chmod(0o600)
        self.policy = promotion.Policy(self.root, owner_uid=os.getuid())

    def approve(self):
        return self.policy.approve(SOURCE, DIGEST, self.config_id)

    def test_unapproved_image_never_reaches_docker(self):
        docker = RecordingDocker()
        with self.assertRaises((ValueError, OSError)):
            self.policy.apply("d" * 64, docker)
        self.assertEqual(docker.commands, [])

    def test_approval_binds_image_config_and_environment(self):
        approval_id = self.approve()
        receipt = json.loads((self.root / "approvals" / f"{approval_id}.json").read_text())
        self.assertEqual(receipt["image_digest"], DIGEST)
        self.assertEqual(receipt["config_digest"], self.config_id)
        self.assertEqual(
            receipt["environment_digest"],
            hashlib.sha256((self.root / "runtime-env.json").read_bytes()).hexdigest(),
        )

    def test_config_tampering_rejected_before_docker(self):
        approval_id = self.approve()
        (self.config / "mailbox.yml").write_text("enabled: false\n")
        docker = RecordingDocker()
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, docker)
        self.assertEqual(docker.commands, [])

    def test_environment_tampering_rejected_before_docker(self):
        approval_id = self.approve()
        (self.root / "runtime-env.json").write_text("{}")
        docker = RecordingDocker()
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, docker)
        self.assertEqual(docker.commands, [])

    def test_group_writable_or_symlink_approval_rejected(self):
        approval_id = self.approve()
        path = self.root / "approvals" / f"{approval_id}.json"
        path.chmod(0o660)
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, RecordingDocker())
        path.chmod(0o600)
        real = path.with_suffix(".real")
        path.rename(real)
        path.symlink_to(real)
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, RecordingDocker())

    def test_profile_is_fixed_and_preserves_literal_dollars(self):
        approval_id = self.approve()
        self.policy.apply(approval_id, RecordingDocker())
        profile = json.loads((self.root / "rendered" / f"{approval_id}.json").read_text())
        self.assertEqual(set(profile["services"]), {"gmail-inbox-bot"})
        service = profile["services"]["gmail-inbox-bot"]
        self.assertEqual(service["image"], "ghcr.io/jmgb/gmail-inbox-bot@" + DIGEST)
        self.assertEqual(service["environment"]["TEST_VALUE"], "literal$${HOME}$$value")
        self.assertNotIn("build", service)
        self.assertNotIn("privileged", service)
        self.assertEqual(service["ports"], ["127.0.0.1:8007:8000"])
        config_mount = next(v for v in service["volumes"] if v["target"] == "/app/config")
        self.assertTrue(config_mount["read_only"])
        self.assertFalse(any("docker.sock" in v["source"] for v in service["volumes"]))

    def test_health_failure_rolls_back_and_does_not_consume_approval(self):
        approval_id = self.approve()
        docker = RecordingDocker(fail_health=True)
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, docker)
        self.assertEqual(docker.commands[-2], ("compose", str(self.root / "baseline.json")))
        self.assertFalse((self.root / "active.json").exists())
        failure = self.root / "last-failure.json"
        self.assertTrue(failure.exists())
        self.assertFalse(json.loads(failure.read_text())["rollback_healthy"])

    def test_success_is_idempotent_without_second_container_restart(self):
        approval_id = self.approve()
        docker = RecordingDocker()
        self.assertEqual(self.policy.apply(approval_id, docker)["status"], "promoted")
        before = list(docker.commands)
        self.assertEqual(self.policy.apply(approval_id, docker)["status"], "already_active")
        self.assertEqual(docker.commands, before + [("health",)])

    def test_config_symlink_is_rejected(self):
        (self.config / "escape.yml").symlink_to(self.root / "runtime-env.json")
        with self.assertRaises(ValueError):
            promotion.config_digest(self.config, os.getuid())

    def test_drifted_runtime_is_reapplied_even_with_same_active_receipt(self):
        approval_id = self.approve()
        docker = RecordingDocker()
        self.policy.apply(approval_id, docker)
        docker.matches_active = False
        before = len(docker.commands)
        self.assertEqual(self.policy.apply(approval_id, docker)["status"], "promoted")
        self.assertGreater(len(docker.commands), before)

    def test_unhealthy_matching_runtime_is_not_reported_as_already_active(self):
        approval_id = self.approve()
        docker = RecordingDocker()
        self.policy.apply(approval_id, docker)
        docker.fail_health = True
        with self.assertRaises(ValueError):
            self.policy.apply(approval_id, docker)


class WorkflowBoundaryTests(unittest.TestCase):
    def test_workflow_no_longer_receives_privileged_deploy_commands_or_registry_pat(self):
        text = (Path(__file__).parents[1] / ".github/workflows/deploy-vps.yml").read_text()
        for forbidden in [
            "username: ubuntu",
            "git fetch origin main",
            "./scripts/vps_deploy.sh",
            "secrets.GHCR_TOKEN",
            "secrets.GHCR_USERNAME",
        ]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertIn("request-promotion:", text)
        self.assertIn("deploy-gmail@", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("VPS_SSH_HOST_KEY", text)

    def test_ci_uses_only_request_key_secret_and_host_variables(self):
        root = Path(__file__).parents[1]
        workflows = "\n".join(
            path.read_text() for path in sorted((root / ".github/workflows").glob("*.y*ml"))
        )
        self.assertTrue(
            "VPS_HOST: ${{ vars.VPS_HOST }}" in workflows,
            "VPS_HOST must come from an Actions variable",
        )
        self.assertTrue(
            "VPS_SSH_HOST_KEY: ${{ vars.VPS_SSH_HOST_KEY }}" in workflows,
            "VPS_SSH_HOST_KEY must come from an Actions variable",
        )
        secret_refs = set(re.findall(r"\$\{\{\s*secrets\.([A-Z_0-9]+)", workflows))
        self.assertEqual(secret_refs, {"GITHUB_TOKEN", "VPS_SSH_KEY"})
        self.assertNotIn("secrets.VPS_HOST", workflows)
        self.assertNotIn("secrets.TELEGRAM_TOKEN", workflows)
        self.assertNotIn("secrets.TELEGRAM_CHAT_ID", workflows)
        self.assertNotIn("notify-failure:", workflows)
        self.assertNotIn("api.telegram.org", workflows)
        self.assertIn("REQUEST_SSH_KEY: ${{ secrets.VPS_SSH_KEY }}", workflows)
        self.assertIn("deploy-gmail@$VPS_HOST", workflows)
        self.assertIn('"request $GITHUB_SHA $IMAGE_DIGEST"', workflows)
        self.assertIn("IMAGE_DIGEST: ${{ needs.build-image.outputs.image_digest }}", workflows)
        self.assertIn("ghcr.io/${OWNER_LOWER}/gmail-inbox-bot", workflows)

    def test_ci_failure_alert_docs_leave_telegram_in_root_monitor_pending(self):
        root = Path(__file__).parents[1]
        docs = (root / "docs/operations/secure-promotion.md").read_text()
        self.assertTrue(
            "VPS_HOST` y `VPS_SSH_HOST_KEY` son variables de Actions" in docs,
            "promotion docs must identify host metadata as Actions variables",
        )
        self.assertTrue(
            "La monitorización de Root fuera de CI está pendiente" in docs,
            "promotion docs must mark external Root monitoring as pending",
        )
        self.assertTrue(
            "No se reenvían fallos de Actions por Telegram" in docs,
            "promotion docs must state failures are not resent to Telegram",
        )


class DockerBoundaryTests(unittest.TestCase):
    def test_approved_digest_gets_a_retention_tag_for_rollback(self):
        runtime = importlib.import_module("ops.gmail_promotion.docker_runtime")
        docker = runtime.Docker(Path("/administrator-owned"))
        ref = "ghcr.io/jmgb/gmail-inbox-bot@" + DIGEST
        with patch.object(docker, "run", side_effect=[json.dumps([ref]), ""]) as run:
            docker.image(ref)
        self.assertEqual(
            run.call_args.args[0],
            ["image", "tag", ref, "ghcr.io/jmgb/gmail-inbox-bot:approved-" + ("b" * 64)],
        )

    def test_docker_does_not_inherit_host_context_or_credential_environment(self):
        runtime = importlib.import_module("ops.gmail_promotion.docker_runtime")
        docker = runtime.Docker(Path("/administrator-owned"))
        with (
            patch.dict(
                os.environ,
                {
                    "DOCKER_HOST": "tcp://attacker:2375",
                    "COMPOSE_FILE": "/attacker.yml",
                    "PROD_SECRET": "must-not-propagate",
                },
            ),
            patch.object(runtime.subprocess, "run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
            docker.run(["version"])
        self.assertEqual(
            run.call_args.args[0][:3], ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
        )
        self.assertNotIn("shell", run.call_args.kwargs)
        for name in ["DOCKER_HOST", "COMPOSE_FILE", "PROD_SECRET"]:
            self.assertNotIn(name, run.call_args.kwargs["env"])

    def test_docker_timeout_is_converted_to_rollback_capable_failure(self):
        runtime = importlib.import_module("ops.gmail_promotion.docker_runtime")
        with patch.object(
            runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 180)
        ):
            with self.assertRaises(ValueError):
                runtime.Docker(Path("/administrator-owned")).run(["version"])

    def test_root_cli_rejects_unprivileged_caller(self):
        if os.geteuid() == 0:
            self.skipTest("Run fixture as an ordinary user")
        cli = Path(__file__).parents[1] / "ops/gmail_promotion/cli.py"
        result = subprocess.run(
            ["/usr/bin/python3", "-I", str(cli), "apply", "a" * 64], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
