"""DATABASES comes from the environment: SQLite by default, Postgres (Meko AMP) when configured."""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.database import database_config

BASE_DIR = Path("/srv/dejafix")
POSTGRES_ENV = {
    "DEJAFIX_DB_HOST": "db.example.test",
    "DEJAFIX_DB_NAME": "dejafix",
    "DEJAFIX_DB_USER": "cloud_admin",
    "DEJAFIX_DB_PASSWORD": "not-a-real-password",
}


class DatabaseConfigTests(SimpleTestCase):
    def test_defaults_to_sqlite(self):
        config = database_config({}, BASE_DIR)
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], BASE_DIR / "db.sqlite3")

    def test_blank_host_still_means_sqlite(self):
        self.assertEqual(database_config({"DEJAFIX_DB_HOST": "  "}, BASE_DIR)["ENGINE"], "django.db.backends.sqlite3")

    def test_postgres_from_env_requires_tls_and_a_timeout(self):
        config = database_config(POSTGRES_ENV, BASE_DIR)
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((config["HOST"], config["PORT"], config["NAME"], config["USER"]), ("db.example.test", 5432, "dejafix", "cloud_admin"))
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")
        self.assertGreater(config["OPTIONS"]["connect_timeout"], 0)
        self.assertTrue(config["CONN_HEALTH_CHECKS"])

    def test_port_and_stricter_sslmode_are_honoured(self):
        config = database_config({**POSTGRES_ENV, "DEJAFIX_DB_PORT": "6543", "DEJAFIX_DB_SSLMODE": "verify-full"}, BASE_DIR)
        self.assertEqual(config["PORT"], 6543)
        self.assertEqual(config["OPTIONS"]["sslmode"], "verify-full")

    def test_missing_credentials_fail_closed(self):
        for missing in ("DEJAFIX_DB_NAME", "DEJAFIX_DB_USER", "DEJAFIX_DB_PASSWORD"):
            env = {key: value for key, value in POSTGRES_ENV.items() if key != missing}
            with self.subTest(missing=missing), self.assertRaisesMessage(ImproperlyConfigured, missing):
                database_config(env, BASE_DIR)

    def test_plaintext_or_unknown_sslmode_is_refused(self):
        for mode in ("disable", "allow", "prefer", "bogus"):
            with self.subTest(mode=mode), self.assertRaisesMessage(ImproperlyConfigured, "DEJAFIX_DB_SSLMODE"):
                database_config({**POSTGRES_ENV, "DEJAFIX_DB_SSLMODE": mode}, BASE_DIR)

    def test_invalid_port_is_refused(self):
        for port in ("abc", "0", "70000"):
            with self.subTest(port=port), self.assertRaisesMessage(ImproperlyConfigured, "DEJAFIX_DB_PORT"):
                database_config({**POSTGRES_ENV, "DEJAFIX_DB_PORT": port}, BASE_DIR)

    def test_tests_use_sqlite_even_when_postgres_is_configured(self):
        config = database_config(POSTGRES_ENV, BASE_DIR, running_tests=True)
        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")

    def test_tests_can_opt_in_to_postgres(self):
        config = database_config({**POSTGRES_ENV, "DEJAFIX_TEST_ON_POSTGRES": "1"}, BASE_DIR, running_tests=True)
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")

    def test_errors_never_contain_the_password(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            database_config({**POSTGRES_ENV, "DEJAFIX_DB_SSLMODE": "disable"}, BASE_DIR)
        self.assertNotIn(POSTGRES_ENV["DEJAFIX_DB_PASSWORD"], str(caught.exception))
