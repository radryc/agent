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


def _write_test_ca(path: Path) -> None:
    """Write a throwaway self-signed CA certificate in PEM form."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "strata-test-ca")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


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
        with patch.multiple(
            tls,
            DEFAULT_CERT_FILE=str(self.dir / "missing.crt"),
            DEFAULT_KEY_FILE=str(self.dir / "missing.key"),
            DEFAULT_CA_FILE=str(self.dir / "missing-ca.crt"),
        ):
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
        for path in (self.cert, self.key):
            path.write_text("test\n")
        _write_test_ca(self.ca)

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self):
        return {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
        }

    def test_ca_bundle_augments_default_trust_store(self):
        verify = tls.http_verify(env=self._env())
        self.assertIsInstance(verify, ssl.SSLContext)
        default_roots = len(ssl.create_default_context().get_ca_certs())
        self.assertGreaterEqual(len(verify.get_ca_certs()), default_roots + 1)
        subjects = [str(entry.get("subject", "")) for entry in verify.get_ca_certs()]
        self.assertTrue(any("strata-test-ca" in subject for subject in subjects))

    def test_default_trust_store_when_off(self):
        self.assertIs(tls.http_verify(env={"STRATA_TLS_MODE": "off"}), True)

    def test_httpx_kwargs_are_empty_when_off(self):
        self.assertEqual(tls.httpx_client_kwargs(env={"STRATA_TLS_MODE": "off"}), {})

    def test_httpx_kwargs_include_ca_and_client_cert(self):
        kwargs = tls.httpx_client_kwargs(env=self._env())
        self.assertIsInstance(kwargs["verify"], ssl.SSLContext)
        self.assertEqual(kwargs["cert"], (str(self.cert), str(self.key)))

    def test_client_cert_can_be_withheld_for_external_endpoints(self):
        kwargs = tls.httpx_client_kwargs(
            env=self._env(), include_client_cert=False
        )
        self.assertNotIn("cert", kwargs)
        self.assertIsInstance(kwargs["verify"], ssl.SSLContext)


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

    def test_explicit_http_is_preserved_when_required(self):
        self.assertEqual(
            tls.normalize_url("http://guardian:8090", env={"STRATA_TLS_MODE": "required"}),
            "http://guardian:8090",
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
        self.assertEqual(cfg.doctor_query_url, "http://doctor.local:18080")
        self.assertEqual(cfg.llm_base_url, "https://llm.local:8080")


class ServerLauncherTest(unittest.TestCase):
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

    def test_launcher_passes_tls_kwargs_without_starting_a_server(self):
        from app import server

        env = {
            "STRATA_TLS_MODE": "required",
            "STRATA_TLS_CERT_FILE": str(self.cert),
            "STRATA_TLS_KEY_FILE": str(self.key),
            "STRATA_TLS_CA_FILE": str(self.ca),
            "STRATA_TLS_REQUIRE_CLIENT_CERT": "true",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            server.uvicorn, "run"
        ) as run:
            rc = server.main(
                ["--app", "app.main:app", "--host", "127.0.0.1", "--port", "9999"]
            )
        self.assertEqual(rc, 0)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["ssl_certfile"], str(self.cert))
        self.assertEqual(kwargs["ssl_keyfile"], str(self.key))
        self.assertEqual(kwargs["ssl_ca_certs"], str(self.ca))
        self.assertEqual(kwargs["ssl_cert_reqs"], ssl.CERT_REQUIRED)

    def test_launcher_fails_closed_on_invalid_config(self):
        from app import server

        with patch.dict(
            os.environ,
            {
                "STRATA_TLS_MODE": "required",
                "STRATA_TLS_CERT_FILE": str(self.dir / "missing.crt"),
                "STRATA_TLS_KEY_FILE": str(self.dir / "missing.key"),
                "STRATA_TLS_CA_FILE": str(self.dir / "missing-ca.crt"),
            },
            clear=False,
        ), patch.object(server.uvicorn, "run") as run:
            rc = server.main([])
        self.assertEqual(rc, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
