from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from config.env import load_dotenv, parse_dotenv
from dejafix.meko import MekoConfig, MekoConfigError


class MekoConfigTests(SimpleTestCase):
    def test_missing_api_key_rejected(self):
        with self.assertRaisesMessage(MekoConfigError, "MEKO_API_KEY is not set"):
            MekoConfig(mcp_url="https://mcp.mekodata.ai/mcp", api_key="")

    def test_plain_http_to_remote_host_rejected(self):
        with self.assertRaisesMessage(MekoConfigError, "must use https"):
            MekoConfig(mcp_url="http://mcp.mekodata.ai/mcp", api_key="k")

    def test_plain_http_to_localhost_allowed(self):
        MekoConfig(mcp_url="http://127.0.0.1:7777/mcp", api_key="k")

    def test_timeout_out_of_range_rejected(self):
        for timeout in (0, 301):
            with self.subTest(timeout=timeout), self.assertRaises(MekoConfigError):
                MekoConfig(mcp_url="https://m.test/mcp", api_key="k", timeout_seconds=timeout)

    def test_datapack_id_must_be_a_uuid(self):
        with self.assertRaisesMessage(MekoConfigError, "MEKO_DATAPACK_ID must be the datapack's UUID"):
            MekoConfig(mcp_url="https://m.test/mcp", api_key="k", datapack_id="support-kb")

    def test_repr_hides_api_key(self):
        self.assertNotIn("super-secret", repr(MekoConfig(mcp_url="https://m.test/mcp", api_key="super-secret")))

    @override_settings(MEKO_MCP_URL="https://m.test/mcp", MEKO_API_KEY="k", MEKO_TIMEOUT_SECONDS=12.0)
    def test_from_settings(self):
        config = MekoConfig.from_settings()
        self.assertEqual((config.mcp_url, config.timeout_seconds), ("https://m.test/mcp", 12.0))


class DotenvTests(SimpleTestCase):
    def test_parse_handles_comments_quotes_export_and_unicode(self):
        parsed = parse_dotenv('# comment\n\nexport A=1\nB="two words"\nC=\'x=y\'\nNOEQUALS\nD=مرحبا\n')
        self.assertEqual(parsed, {"A": "1", "B": "two words", "C": "x=y", "D": "مرحبا"})

    def test_load_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("﻿DEJAFIX_T1=from-file\nDEJAFIX_T2=from-file\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DEJAFIX_T1": "already-set"}):
                load_dotenv(path)
                self.assertEqual((os.environ["DEJAFIX_T1"], os.environ["DEJAFIX_T2"]), ("already-set", "from-file"))

    def test_missing_file_is_ignored(self):
        load_dotenv(Path("does-not-exist.env"))
