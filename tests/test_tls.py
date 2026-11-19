from __future__ import annotations

import os
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.security import tls  # noqa: E402


class TLSModeTest(unittest.TestCase):
    def test_empty_mode_is_off(self):
        self.assertEqual(tls.parse_tls_mode(""), tls.MODE_OFF)
        self.assertEqual(tls.parse_tls_mode(None), tls.MODE_OFF)
        self.assertEqual(tls.parse_tls_mode("   "), tls.MODE_OFF)

    def test_modes_are_case_insensitive(self):
        self.assertEqual(tls.parse_tls_mode("OFF"), tls.MODE_OFF)
        self.assertEqual(tls.parse_tls_mode(" Optional "), tls.MODE_OPTIONAL)
        self.assertEqual(tls.parse_tls_mode("REQUIRED"), tls.MODE_REQUIRED)

    def test_invalid_mode_raises(self):
        with self.assertRaises(tls.TLSConfigError):
            tls.parse_tls_mode("sometimes")

    def test_invalid_require_client_cert_raises(self):
        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_REQUIRE_CLIENT_CERT": "maybe",
        }
        with self.assertRaises(tls.TLSConfigError):
            tls.load_tls_settings(env)


class TLSFileValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cert = self.dir / "tls.crt"
        self.key = self.dir / "tls.key"
        self.ca = self.dir / "ca.crt"
        for path in (self.cert, self.key, self.ca):
            path.write_text("test\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self, **overrides):
        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
        }
        env.update(overrides)
        return env

    def test_off_does_not_require_files(self):
        cfg = tls.load_tls_settings(
            {
                "STRATA_TLS_MODE": "off",
                "STRATA_TLS_CERT_FILE": str(self.dir / "missing.crt"),
                "STRATA_TLS_KEY_FILE": str(self.dir / "missing.key"),
                "STRATA_TLS_CA_FILE": str(self.dir / "missing-ca.crt"),
            }
        )
        self.assertFalse(cfg.enabled)
        self.assertEqual(tls.uvicorn_tls_kwargs(cfg), {})

    def test_required_missing_cert_raises(self):
        env = self._env(STRATA_TLS_CERT_FILE=str(self.dir / "missing.crt"))
        with self.assertRaises(tls.TLSConfigError):
            tls.load_tls_settings(env)

    def test_required_missing_key_raises(self):
        env = self._env(STRATA_TLS_KEY_FILE=str(self.dir / "missing.key"))
        with self.assertRaises(tls.TLSConfigError):
            tls.load_tls_settings(env)

    def test_required_missing_ca_raises(self):
        env = self._env(STRATA_TLS_CA_FILE=str(self.dir / "missing-ca.crt"))
        with self.assertRaises(tls.TLSConfigError):
            tls.load_tls_settings(env)

    def test_required_with_files_enables_tls(self):
        cfg = tls.load_tls_settings(self._env())
        self.assertEqual(cfg.mode, tls.MODE_REQUIRED)
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.require_client_cert)

    def test_optional_without_mounted_pki_stays_plaintext(self):
        cfg = tls.load_tls_settings({"STRATA_TLS_MODE": "optional"})
        self.assertEqual(cfg.mode, tls.MODE_OPTIONAL)
        self.assertFalse(cfg.enabled)
        self.assertEqual(tls.uvicorn_tls_kwargs(cfg), {})

    def test_optional_with_explicit_missing_cert_raises(self):
        env = {
            "STRATA_TLS_MODE": "optional",
            "STRATA_TLS_CERT_FILE": str(self.dir / "missing.crt"),
            "STRATA_TLS_KEY_FILE": str(self.key),
        }
        with self.assertRaises(tls.TLSConfigError):
            tls.load_tls_settings(env)

    def test_optional_with_files_enables_tls(self):
        cfg = tls.load_tls_settings(
            {
                "STRATA_TLS_MODE": "optional",
                "STRATA_TLS_CERT_FILE": str(self.cert),
                "STRATA_TLS_KEY_FILE": str(self.key),
                "STRATA_TLS_CA_FILE": str(self.ca),
            }
        )
        self.assertTrue(cfg.enabled)
        kwargs = tls.uvicorn_tls_kwargs(cfg)
        self.assertEqual(kwargs["ssl_certfile"], str(self.cert))
        self.assertEqual(kwargs["ssl_keyfile"], str(self.key))
        self.assertEqual(kwargs["ssl_ca_certs"], str(self.ca))


class UvicornKwargsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cert = self.dir / "tls.crt"
        self.key = self.dir / "tls.key"
        self.ca = self.dir / "ca.crt"
        for path in (self.cert, self.key, self.ca):
            path.write_text("test\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self, **overrides):
        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
        }
        env.update(overrides)
        return env

    def test_off_returns_no_kwargs(self):
        self.assertEqual(tls.uvicorn_kwargs(env={"STRATA_TLS_MODE": "off"}), {})

    def test_required_returns_ssl_kwargs(self):
        kwargs = tls.uvicorn_tls_kwargs(env=self._env())
        self.assertEqual(
            kwargs,
            {
                "ssl_certfile": str(self.cert),
                "ssl_keyfile": str(self.key),
                "ssl_ca_certs": str(self.ca),
                "ssl_cert_reqs": ssl.CERT_NONE,
            },
        )

    def test_client_cert_verification_is_required_only_when_opted_in(self):
        kwargs = tls.uvicorn_tls_kwargs(
            env=self._env(STRATA_TLS_REQUIRE_CLIENT_CERT="true")
        )
        self.assertEqual(kwargs["ssl_cert_reqs"], ssl.CERT_REQUIRED)
        self.assertFalse(tls.load_tls_settings(self._env()).verify_client_cert)


class HTTPVerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cert = self.dir / "tls.crt"
        self.key = self.dir / "tls.key"
        self.ca = self.dir / "ca.crt"
        for path in (self.cert, self.key, self.ca):
            path.write_text("test\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ca_bundle_is_used_when_configured(self):
        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
        }
        self.assertEqual(tls.http_verify(env=env), str(self.ca))

    def test_default_trust_store_when_off(self):
        self.assertIs(tls.http_verify(env={"STRATA_TLS_MODE": "off"}), True)

    def test_httpx_kwargs_are_empty_when_off(self):
        self.assertEqual(tls.httpx_client_kwargs(env={"STRATA_TLS_MODE": "off"}), {})

    def test_httpx_kwargs_include_ca_and_client_cert(self):
        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
        }
        kwargs = tls.httpx_client_kwargs(env=env)
        self.assertEqual(kwargs["verify"], str(self.ca))
        self.assertEqual(kwargs["cert"], (str(self.cert), str(self.key)))


class URLNormalizationTest(unittest.TestCase):
    def test_scheme_less_url_uses_https_when_required(self):
        self.assertEqual(
            tls.normalize_url("guardian:8090", env={"STRATA_TLS_MODE": "required"}),
            "https://guardian:8090",
        )

    def test_scheme_less_url_uses_http_when_off(self):
        self.assertEqual(
            tls.normalize_url("guardian:8090", env={"STRATA_TLS_MODE": "off"}),
            "http://guardian:8090",
        )

    def test_scheme_less_url_uses_http_when_optional(self):
        self.assertEqual(
            tls.normalize_url("guardian:8090", env={"STRATA_TLS_MODE": "optional"}),
            "http://guardian:8090",
        )

    def test_explicit_http_is_upgraded_when_required(self):
        self.assertEqual(
            tls.normalize_url("http://guardian:8090", env={"STRATA_TLS_MODE": "required"}),
            "https://guardian:8090",
        )

    def test_explicit_http_is_untouched_when_off(self):
        self.assertEqual(
            tls.normalize_url("http://guardian:8090", env={"STRATA_TLS_MODE": "off"}),
            "http://guardian:8090",
        )

    def test_https_is_always_untouched(self):
        for mode in ("off", "optional", "required"):
            self.assertEqual(
                tls.normalize_url("https://guardian:8090", env={"STRATA_TLS_MODE": mode}),
                "https://guardian:8090",
            )

    def test_empty_url_stays_empty(self):
        self.assertEqual(tls.normalize_url("", env={"STRATA_TLS_MODE": "required"}), "")

    def test_settings_normalize_service_urls(self):
        from app.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / "tls.crt"
            key = Path(tmp) / "tls.key"
            ca = Path(tmp) / "ca.crt"
            for path in (cert, key, ca):
                path.write_text("test\n")
            env = {
                "STRATA_TLS_MODE": "required",
                "STRATA_TLS_CERT_FILE": str(cert),
                "STRATA_TLS_KEY_FILE": str(key),
                "STRATA_TLS_CA_FILE": str(ca),
                "GUARDIAN_URL": "guardian.local:8090",
                "DOCTOR_QUERY_URL": "http://doctor.local:18080",
                "LLM_BASE_URL": "llm.local:8080",
            }
            with patch.dict(os.environ, env, clear=False):
                cfg = Settings(_env_file=None)
        self.assertEqual(cfg.guardian_url, "https://guardian.local:8090")
        self.assertEqual(cfg.doctor_query_url, "https://doctor.local:18080")
        self.assertEqual(cfg.llm_base_url, "https://llm.local:8080")


if __name__ == "__main__":
    unittest.main()
