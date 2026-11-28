"""`manage.py provision_db`: the app's Postgres cluster on Meko AMP, reproducible from the repo."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings
from mcp.server.mcpserver import MCPServer

from config.env import parse_dotenv
from dejafix.meko import MekoSession
from dejafix.tests.fakes import TEST_DATAPACK_ID
from support.management.commands.provision_db import Command

FAKE_SETTINGS = {
    "MEKO_MCP_URL": "https://meko.test/mcp",
    "MEKO_API_KEY": "test-key",
    "MEKO_TIMEOUT_SECONDS": 5.0,
    "MEKO_DATAPACK_ID": TEST_DATAPACK_ID,
}
PUBLIC_CIDR = "81.2.69.160/32"
CLUSTER_ID = "75382b44-0000-4000-8000-000000000001"
BRANCH_ID = "f79490ae-0000-4000-8000-000000000002"
ENDPOINT_ADDRESS = "ep-1.db.meko.test:20008"


@dataclass
class FakeAmp:
    """Meko's AMP tools with the reply shapes the live service returned (decision log, R7)."""

    env_path: Path
    used_cores: float = 0.0
    existing_clusters: list[dict] = field(default_factory=list)
    endpoint_ready: bool = True
    login_user: str = "admin"
    create_error: dict | None = None
    calls: list[tuple[str, dict]] = field(default_factory=list)
    env_at_create: dict[str, str] = field(default_factory=dict)

    def build(self) -> MCPServer:
        server = MCPServer("fake-meko-amp")

        @server.tool()
        def conversation_create(
            title: str | None = None,
            session_id: str | None = None,
            agent_id: str | None = None,
            datapack_id: str | None = None,
        ) -> str:
            self.calls.append(("conversation_create", {"session_id": session_id, "agent_id": agent_id}))
            return json.dumps({"id": f"conv-{session_id}", "agent_id": agent_id})

        @server.tool()
        def amp_core_budget(datapack_id: str = "", conversation_id: str = "") -> str:
            self.calls.append(("amp_core_budget", {"conversation_id": conversation_id}))
            return json.dumps(
                {"tier": "free", "used_cores": self.used_cores, "max_cores": 0.5, "at_limit": self.used_cores >= 0.5}
            )

        @server.tool()
        def amp_cluster_list(datapack_id: str = "", conversation_id: str = "") -> str:
            self.calls.append(("amp_cluster_list", {"conversation_id": conversation_id}))
            return json.dumps(self.existing_clusters)

        @server.tool()
        def amp_cluster_create(
            name: str,
            datapack_id: str = "",
            with_writer: bool = False,
            cpu: str | None = None,
            password: str | None = None,
            connectivity_mode: str | None = None,
            allowed_cidrs: list[str] | None = None,
            conversation_id: str = "",
        ) -> str:
            self.calls.append(
                (
                    "amp_cluster_create",
                    {
                        "name": name,
                        "with_writer": with_writer,
                        "cpu": cpu,
                        "password": password,
                        "connectivity_mode": connectivity_mode,
                        "allowed_cidrs": allowed_cidrs,
                        "conversation_id": conversation_id,
                    },
                )
            )
            self.env_at_create = read_env(self.env_path)
            if self.create_error is not None:
                return json.dumps(self.create_error)
            return json.dumps(
                {
                    "id": CLUSTER_ID,
                    "name": name,
                    "default_branch": {"branch_id": BRANCH_ID, "cluster_id": CLUSTER_ID, "name": "main"},
                    "password_notice": "Stored as a SCRAM verifier only.",
                }
            )

        @server.tool()
        def amp_cluster_get(cluster_id: str, datapack_id: str = "", conversation_id: str = "") -> str:
            self.calls.append(("amp_cluster_get", {"cluster_id": cluster_id, "conversation_id": conversation_id}))
            return json.dumps(
                {"id": cluster_id, "name": "dejafix-app", "default_branch": {"branch_id": BRANCH_ID, "name": "main"}}
            )

        @server.tool()
        def amp_compute_list(
            cluster_id: str, branch_id: str, datapack_id: str = "", conversation_id: str = ""
        ) -> str:
            self.calls.append(
                ("amp_compute_list", {"cluster_id": cluster_id, "branch_id": branch_id, "conversation_id": conversation_id})
            )
            if not self.endpoint_ready:
                return json.dumps([])
            return json.dumps(
                [
                    {
                        "id": "ep-1",
                        "cluster_id": cluster_id,
                        "branch_id": branch_id,
                        "role": "read_write",
                        "address": ENDPOINT_ADDRESS,
                        "login_user": self.login_user,
                    }
                ]
            )

        return server

    def tool_calls(self) -> list[str]:
        return [name for name, _ in self.calls]


def read_env(path: Path) -> dict[str, str]:
    return parse_dotenv(path.read_text(encoding="utf-8")) if path.exists() else {}


@override_settings(**FAKE_SETTINGS)
class ProvisionDbCommandTests(SimpleTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_path = Path(tmp.name) / ".env"
        self.env_path.write_text("MEKO_API_KEY=placeholder\n", encoding="utf-8")
        self.amp = FakeAmp(env_path=self.env_path)

    def provision(self, *args: str) -> str:
        stdout, stderr = StringIO(), StringIO()
        server = self.amp.build()
        factory = lambda config: MekoSession(config, server=server)  # noqa: E731
        with mock.patch.object(Command, "session_factory", staticmethod(factory)):
            try:
                call_command("provision_db", "--env-file", str(self.env_path), *args, stdout=stdout, stderr=stderr)
            finally:
                self.output = stdout.getvalue() + stderr.getvalue()
        return self.output

    def create_call(self) -> dict:
        return next(args for name, args in self.amp.calls if name == "amp_cluster_create")

    def test_creates_cluster_at_free_tier_with_given_cidr(self):
        out = self.provision("--allow-cidr", PUBLIC_CIDR)

        self.assertEqual(
            self.amp.tool_calls(),
            ["conversation_create", "amp_core_budget", "amp_cluster_list", "amp_cluster_create", "amp_compute_list"],
        )
        create = self.create_call()
        self.assertEqual(create["name"], "dejafix-app")
        self.assertTrue(create["with_writer"])
        self.assertIsNone(create["cpu"], "omitting cpu keeps the free-tier 0.5 vCPU default")
        self.assertEqual(create["connectivity_mode"], "ingress_external")
        self.assertEqual(create["allowed_cidrs"], [PUBLIC_CIDR])
        self.assertTrue(all(args["conversation_id"] == "conv-dejafix-dba" for name, args in self.amp.calls[1:]))

        env = read_env(self.env_path)
        self.assertEqual(env["DEJAFIX_AMP_CLUSTER_ID"], CLUSTER_ID)
        self.assertEqual(env["DEJAFIX_DB_HOST"], "ep-1.db.meko.test")
        self.assertEqual(env["DEJAFIX_DB_PORT"], "20008")
        self.assertEqual(env["DEJAFIX_DB_USER"], "admin")
        self.assertEqual(env["DEJAFIX_DB_NAME"], "postgres")
        self.assertEqual(env["DEJAFIX_DB_PASSWORD"], create["password"])
        self.assertGreaterEqual(len(create["password"]), 32)
        self.assertEqual(env["MEKO_API_KEY"], "placeholder", "existing lines are kept")
        self.assertIn("manage.py migrate", out)

    def test_writes_env_before_create_call(self):
        self.provision("--allow-cidr", PUBLIC_CIDR)

        self.assertEqual(self.amp.env_at_create.get("DEJAFIX_DB_PASSWORD"), self.create_call()["password"])

    def test_existing_cluster_shows_status_without_creating(self):
        self.env_path.write_text(
            f"DEJAFIX_DB_PASSWORD=saved-secret-value\nDEJAFIX_AMP_CLUSTER_ID={CLUSTER_ID}\n", encoding="utf-8"
        )

        out = self.provision()

        self.assertEqual(self.amp.tool_calls(), ["conversation_create", "amp_cluster_get", "amp_compute_list"])
        env = read_env(self.env_path)
        self.assertEqual(env["DEJAFIX_DB_PASSWORD"], "saved-secret-value")
        self.assertEqual(env["DEJAFIX_DB_HOST"], "ep-1.db.meko.test")
        self.assertIn("dejafix-app", out)

    def test_status_run_does_not_overwrite_values_already_set(self):
        self.env_path.write_text(
            f"DEJAFIX_DB_PASSWORD=saved-secret-value\nDEJAFIX_AMP_CLUSTER_ID={CLUSTER_ID}\n"
            "DEJAFIX_DB_HOST=custom.example\nDEJAFIX_DB_PORT=20008\nDEJAFIX_DB_USER=admin\nDEJAFIX_DB_NAME=dejafix\n",
            encoding="utf-8",
        )
        before = self.env_path.read_text(encoding="utf-8")

        self.provision()

        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)

    def test_insufficient_budget_makes_no_create_call(self):
        self.amp.used_cores = 0.5
        before = self.env_path.read_text(encoding="utf-8")

        with self.assertRaisesMessage(CommandError, "vCPU"):
            self.provision("--allow-cidr", PUBLIC_CIDR)

        self.assertNotIn("amp_cluster_create", self.amp.tool_calls())
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)

    def test_rejects_invalid_or_too_wide_cidr(self):
        before = self.env_path.read_text(encoding="utf-8")
        for cidr in ("not-an-ip", "81.2.0.0/23", "0.0.0.0/0", "81.2.69.160/24", "192.168.1.5/32", "81.2.69.160"):
            with self.subTest(cidr=cidr):
                with self.assertRaises(CommandError):
                    self.provision("--allow-cidr", cidr)
        self.assertEqual(self.amp.calls, [])
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)

    def test_new_cluster_needs_an_allow_list(self):
        with self.assertRaisesMessage(CommandError, "--allow-cidr"):
            self.provision()
        self.assertEqual(self.amp.calls, [])

    def test_refuses_to_overwrite_existing_db_vars(self):
        self.env_path.write_text("DEJAFIX_DB_HOST=db.example\n", encoding="utf-8")

        with self.assertRaisesMessage(CommandError, "DEJAFIX_DB_HOST"):
            self.provision("--allow-cidr", PUBLIC_CIDR)

        self.assertEqual(self.amp.calls, [])
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), "DEJAFIX_DB_HOST=db.example\n")

    def test_existing_cluster_with_same_name_is_not_created_again(self):
        self.amp.existing_clusters = [{"id": CLUSTER_ID, "name": "dejafix-app"}]

        with self.assertRaisesMessage(CommandError, CLUSTER_ID):
            self.provision("--allow-cidr", PUBLIC_CIDR)

        self.assertNotIn("amp_cluster_create", self.amp.tool_calls())

    def test_password_never_in_output_or_errors(self):
        self.provision("--allow-cidr", PUBLIC_CIDR)
        password = read_env(self.env_path)["DEJAFIX_DB_PASSWORD"]
        self.assertNotIn(password, self.output)

        # A failing create whose error echoes the password back.
        self.env_path.write_text(f"DEJAFIX_DB_PASSWORD={password}\n", encoding="utf-8")
        self.amp.calls.clear()
        self.amp.create_error = {"error": "invalid_password", "detail": f"rejected password {password}"}
        with self.assertRaises(CommandError) as caught, self.assertLogs("dejafix", "WARNING") as logs:
            self.provision("--allow-cidr", PUBLIC_CIDR)
        self.assertNotIn(password, "\n".join(logs.output))
        self.assertNotIn(password, str(caught.exception))
        self.assertNotIn(password, self.output)

    def test_create_failure_reports_and_keeps_saved_password(self):
        self.amp.create_error = {"error": "FREE_TIER_CORE_LIMIT", "detail": "free tier allows 0.5 vCPU"}

        with self.assertRaisesMessage(CommandError, "FREE_TIER_CORE_LIMIT"), self.assertLogs("dejafix", "WARNING"):
            self.provision("--allow-cidr", PUBLIC_CIDR)

        env = read_env(self.env_path)
        saved_password = env["DEJAFIX_DB_PASSWORD"]
        self.assertNotIn("DEJAFIX_AMP_CLUSTER_ID", env)

        # The retry reuses the saved password instead of adding a second one.
        self.amp.create_error = None
        self.provision("--allow-cidr", PUBLIC_CIDR)
        self.assertEqual(self.create_call()["password"], saved_password)
        self.assertEqual(self.env_path.read_text(encoding="utf-8").count("DEJAFIX_DB_PASSWORD="), 1)

    def test_endpoint_not_ready_saves_cluster_and_asks_to_rerun(self):
        self.amp.endpoint_ready = False

        out = self.provision("--allow-cidr", PUBLIC_CIDR)

        env = read_env(self.env_path)
        self.assertEqual(env["DEJAFIX_AMP_CLUSTER_ID"], CLUSTER_ID)
        self.assertNotIn("DEJAFIX_DB_HOST", env)
        self.assertIn("run this command again", out)

    @override_settings(MEKO_DATAPACK_ID="")
    def test_without_datapack_calls_nothing(self):
        with self.assertRaisesMessage(CommandError, "MEKO_DATAPACK_ID"):
            self.provision("--allow-cidr", PUBLIC_CIDR)
        self.assertEqual(self.amp.calls, [])

    def test_meko_values_with_line_breaks_never_reach_env(self):
        # Week 12 review: a value with a line break could append its own line to .env, and
        # parse_dotenv keeps the last duplicate key.
        self.amp.login_user = "admin\nMEKO_MCP_URL=https://evil.example"
        with self.assertRaises(CommandError):
            self.provision("--allow-cidr", PUBLIC_CIDR)
        self.assertNotIn("MEKO_MCP_URL", read_env(self.env_path))
