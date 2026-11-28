"""HTTPS hardening is opt-in: off for the local demo over http://, on for a real deployment."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.security import DEFAULT_HSTS_SECONDS, https_settings


class HttpsSettingsTests(SimpleTestCase):
    def test_off_by_default_so_the_local_http_demo_keeps_working(self):
        self.assertEqual(https_settings({}), {})
        self.assertEqual(https_settings({"DJANGO_HTTPS": "0"}), {})

    def test_on_sets_everything_check_deploy_asks_for(self):
        config = https_settings({"DJANGO_HTTPS": "1"})
        self.assertEqual(
            config,
            {
                "SECURE_SSL_REDIRECT": True,
                "SESSION_COOKIE_SECURE": True,
                "CSRF_COOKIE_SECURE": True,
                "SECURE_HSTS_SECONDS": DEFAULT_HSTS_SECONDS,
            },
        )

    def test_hsts_seconds_can_be_set_and_must_be_a_positive_integer(self):
        self.assertEqual(https_settings({"DJANGO_HTTPS": "true", "DJANGO_HSTS_SECONDS": "31536000"})["SECURE_HSTS_SECONDS"], 31536000)
        for bad in ("-1", "0", "soon", "1.5"):
            with self.subTest(value=bad), self.assertRaises(ImproperlyConfigured):
                https_settings({"DJANGO_HTTPS": "1", "DJANGO_HSTS_SECONDS": bad})
