"""Create the app's Postgres cluster in Meko (AMP) and write its settings to .env.

Usage:
    python manage.py provision_db --allow-cidr <your public IP>/32   # first run: creates the cluster
    python manage.py provision_db                                    # later: status, fills in the endpoint

The password is generated here, saved to .env before the create call (Meko keeps only a
verifier, so a lost reply must not lose it) and never printed. Existing .env values are never
overwritten. Then run `manage.py migrate`.
"""

from __future__ import annotations

import ipaddress
import secrets
from pathlib import Path
from typing import Any

import anyio
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from config.env import parse_dotenv
from dejafix.meko import MekoConfig, MekoError, MekoGateway, MekoSession, MekoToolError

DBA_AGENT_ID = "dejafix-dba"
# A fixed session_id is Meko's dedup key, so every run reuses one setup conversation.
DBA_SESSION_ID = "dejafix-dba"
DEFAULT_CLUSTER_NAME = "dejafix-app"
# AMP's with_writer default (0.5 vCPU / 4Gi) is the whole free tier; omitting cpu keeps it.
WRITER_CORES = 0.5
# Meko refuses allow-list entries broader than these prefixes.
MIN_PREFIX = {4: 24, 6: 48}
PASSWORD_BYTES = 32
# The cluster is DejaFix's alone, so its default database is enough.
DEFAULT_DB_NAME = "postgres"
CLUSTER_ID_VAR = "DEJAFIX_AMP_CLUSTER_ID"
PASSWORD_VAR = "DEJAFIX_DB_PASSWORD"
CONNECTION_VARS = ("DEJAFIX_DB_HOST", "DEJAFIX_DB_PORT", "DEJAFIX_DB_USER", "DEJAFIX_DB_NAME")
REDACTED = "<redacted>"


def allowed_cidr(raw: str) -> str:
    """One public network with an explicit prefix, no broader than Meko accepts."""
    if "/" not in raw:
        raise CommandError("--allow-cidr needs an explicit prefix, e.g. 203.0.113.4/32.")
    try:
        network = ipaddress.ip_network(raw.strip(), strict=True)
    except ValueError as exc:
        raise CommandError(f"--allow-cidr is not a valid network: {exc}") from exc
    if network.prefixlen < MIN_PREFIX[network.version]:
        raise CommandError(f"--allow-cidr may not be broader than /{MIN_PREFIX[network.version]}.")
    if not network.is_global:
        raise CommandError("--allow-cidr must be your public IP address, not a private or reserved one.")
    return str(network)


def endpoint_settings(computes: Any) -> dict[str, str]:
    """DEJAFIX_DB_* values from amp_compute_list's read-write endpoint, or {} if it isn't up yet."""
    for compute in computes if isinstance(computes, list) else []:
        if not isinstance(compute, dict) or compute.get("role") != "read_write":
            continue
        host, _, port = str(compute.get("address") or "").rpartition(":")
        user = str(compute.get("login_user") or "")
        if host and port.isdigit() and user:
            return {"DEJAFIX_DB_HOST": host, "DEJAFIX_DB_PORT": port, "DEJAFIX_DB_USER": user}
    return {}


class Command(BaseCommand):
    help = "Create (or check) the app's Postgres cluster in Meko AMP and write its settings to .env."

    # Tests swap this for a session bound to an in-process fake server.
    session_factory = MekoSession

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--allow-cidr", help="Your public IP as a CIDR (e.g. 203.0.113.4/32). Needed to create.")
        parser.add_argument("--name", default=DEFAULT_CLUSTER_NAME, help="Cluster name for a new cluster.")
        parser.add_argument("--env-file", type=Path, default=None, help="Defaults to .env in the project root.")

    def handle(self, *args: Any, **options: Any) -> None:
        self.env_path: Path = options["env_file"] or Path(settings.BASE_DIR) / ".env"
        env = self._read_env()
        self.password = env.get(PASSWORD_VAR, "")
        cluster_id = env.get(CLUSTER_ID_VAR, "")

        cidr = ""
        if not cluster_id:
            already_set = [name for name in CONNECTION_VARS if env.get(name)]
            if already_set:
                raise CommandError(
                    f"{self.env_path} already sets {', '.join(already_set)} without {CLUSTER_ID_VAR}. "
                    "Remove them to create a new cluster; nothing was changed."
                )
            if not options["allow_cidr"]:
                raise CommandError("Creating a cluster needs --allow-cidr <your public IP>/32.")
            cidr = allowed_cidr(options["allow_cidr"])

        try:
            config = MekoConfig.from_settings()
            if not config.datapack_id:
                raise CommandError("MEKO_DATAPACK_ID is not set: the cluster is created in that datapack.")
            anyio.run(self._run, config, env, cluster_id, cidr, options["name"])
        except MekoError as exc:
            raise CommandError(f"Meko AMP call failed: {self._redact(str(exc))}") from exc

    async def _run(self, config: MekoConfig, env: dict[str, str], cluster_id: str, cidr: str, name: str) -> None:
        async with self.session_factory(config) as meko:
            dba = await self._open_conversation(meko)
            if cluster_id:
                cluster = await dba.call("amp_cluster_get", cluster_id=cluster_id)
                self.stdout.write(f"Cluster {cluster.get('name', '?')} ({cluster_id}) exists.")
            else:
                cluster = await self._create(dba, cidr, name)
                cluster_id = str(cluster.get("id") or "")
                if not cluster_id:
                    raise CommandError("amp_cluster_create replied without a cluster id; check the Meko portal.")
                self._append_env({CLUSTER_ID_VAR: cluster_id})
                env[CLUSTER_ID_VAR] = cluster_id
                self.stdout.write(self.style.SUCCESS(f"Created cluster {name} ({cluster_id})."))
            branch_id = str((cluster.get("default_branch") or {}).get("branch_id") or "")
            computes = await dba.call("amp_compute_list", cluster_id=cluster_id, branch_id=branch_id)

        endpoint = endpoint_settings(computes)
        if not endpoint:
            self.stdout.write("The read-write endpoint is not up yet: run this command again in a minute.")
            return
        endpoint["DEJAFIX_DB_NAME"] = DEFAULT_DB_NAME
        missing = {key: value for key, value in endpoint.items() if not env.get(key)}
        self._append_env(missing)
        self.stdout.write(f"Endpoint ready. Wrote to .env: {', '.join(missing) or 'nothing (all set already)'}.")
        self.stdout.write(self.style.SUCCESS("Next: python manage.py migrate"))

    async def _open_conversation(self, meko: MekoGateway) -> MekoGateway:
        dba = meko.as_agent(DBA_AGENT_ID)
        conversation = await dba.call("conversation_create", title="DejaFix database setup", session_id=DBA_SESSION_ID)
        conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
        if not isinstance(conversation_id, str):
            raise MekoToolError("conversation_create", "unexpected_reply", "no conversation id in the reply")
        return dba.in_conversation(conversation_id)

    async def _create(self, dba: MekoGateway, cidr: str, name: str) -> dict[str, Any]:
        budget = await dba.call("amp_core_budget")
        try:
            free_cores = float(budget["max_cores"]) - float(budget["used_cores"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError("amp_core_budget replied in an unexpected shape; nothing was created.") from exc
        if budget.get("at_limit") or free_cores < WRITER_CORES:
            raise CommandError(
                f"The {budget.get('tier', '?')} tier has {free_cores:g} vCPU left; the cluster needs {WRITER_CORES}."
            )

        clusters = await dba.call("amp_cluster_list")
        same_name = [c for c in clusters if isinstance(c, dict) and c.get("name") == name] if isinstance(clusters, list) else []
        if same_name:
            raise CommandError(
                f"A cluster named {name} already exists ({same_name[0].get('id')}). Put its id in .env as "
                f"{CLUSTER_ID_VAR} and run this command again to fill in the endpoint."
            )

        if not self.password:
            # Saved before the call: Meko keeps only a verifier, so a lost reply must not lose it.
            self.password = secrets.token_urlsafe(PASSWORD_BYTES)
            self._append_env({PASSWORD_VAR: self.password}, header="# Meko AMP Postgres (manage.py provision_db)")
        reply = await dba.call(
            "amp_cluster_create",
            name=name,
            with_writer=True,
            connectivity_mode="ingress_external",
            allowed_cidrs=[cidr],
            password=self.password,
        )
        if not isinstance(reply, dict):
            raise CommandError("amp_cluster_create replied in an unexpected shape; check the Meko portal.")
        return reply

    def _read_env(self) -> dict[str, str]:
        if not self.env_path.is_file():
            return {}
        return parse_dotenv(self.env_path.read_text(encoding="utf-8-sig"))

    def _append_env(self, values: dict[str, str], header: str = "") -> None:
        """Append only: existing lines (and any secrets in them) are left as they are."""
        if not values:
            return
        existing = self.env_path.read_text(encoding="utf-8-sig") if self.env_path.is_file() else ""
        lines = ["" if not existing or existing.endswith("\n") else "\n"]
        if header:
            lines.append(f"\n{header}\n")
        lines += [f"{key}={value}\n" for key, value in values.items()]
        with self.env_path.open("a", encoding="utf-8") as env_file:
            env_file.write("".join(lines))

    def _redact(self, text: str) -> str:
        return text.replace(self.password, REDACTED) if self.password else text
