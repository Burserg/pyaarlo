import importlib.util
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import tests.arlo
from pyaarlo.backend import ArloBackEnd
from pyaarlo.tfa import (
    Arlo2FACloudflare,
    Arlo2FAConsole,
    Arlo2FAImap,
    Arlo2FARestAPI,
)

WORKER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "examples", "cloudflare-tfa", "worker.py",
)
SAMPLE_EML_PATH = os.path.join(os.path.dirname(WORKER_PATH), "sample-arlo.eml")


def _load_worker():
    spec = importlib.util.spec_from_file_location("cf_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _response(status_code=200, json_body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    return response


class TestGetTfa(TestCase):
    def _dispatch(self, **kwargs):
        fake = SimpleNamespace(_arlo=tests.arlo.PyArlo(**kwargs))
        return ArloBackEnd._get_tfa(fake)

    def test_console_default(self):
        self.assertIsInstance(self._dispatch(), Arlo2FAConsole)
        self.assertIsInstance(self._dispatch(tfa_source="console"), Arlo2FAConsole)

    def test_imap(self):
        self.assertIsInstance(self._dispatch(tfa_source="imap"), Arlo2FAImap)

    def test_rest_api(self):
        self.assertIsInstance(self._dispatch(tfa_source="rest-api"), Arlo2FARestAPI)

    def test_cloudflare(self):
        self.assertIsInstance(self._dispatch(tfa_source="cloudflare"), Arlo2FACloudflare)

    def test_custom_object_passthrough(self):
        custom = object()
        self.assertIs(self._dispatch(tfa_source=custom), custom)


class TestArlo2FACloudflare(TestCase):
    def _tfa(self, **kwargs):
        args = {
            "tfa_source": "cloudflare",
            "tfa_host": "pyaarlo-tfa.example.workers.dev",
            "tfa_username": "arlo-tfa@example.com",
            "tfa_password": "secret-token",
            "tfa_timeout": 0.01,
            "tfa_total_timeout": 0.2,
        }
        args.update(kwargs)
        args = {k: v for k, v in args.items() if v is not None}
        return Arlo2FACloudflare(tests.arlo.PyArlo(**args))

    @patch("pyaarlo.tfa.requests.post")
    def test_start_requires_explicit_host(self, mock_post):
        tfa = self._tfa(tfa_host=None)
        self.assertFalse(tfa.start())
        mock_post.assert_not_called()
        self.assertIsNotNone(tfa._arlo.last_error)

    @patch("pyaarlo.tfa.requests.post")
    def test_start_requires_explicit_password(self, mock_post):
        tfa = self._tfa(tfa_password=None, password="real-arlo-password")
        self.assertFalse(tfa.start())
        mock_post.assert_not_called()

    @patch("pyaarlo.tfa.requests.post")
    def test_start_clears(self, mock_post):
        mock_post.return_value = _response(200)
        tfa = self._tfa()
        self.assertTrue(tfa.start())
        mock_post.assert_called_once_with(
            "https://pyaarlo-tfa.example.workers.dev/clear",
            params={"email": "arlo-tfa@example.com"},
            headers={"Authorization": "Bearer secret-token"},
            timeout=10,
        )

    @patch("pyaarlo.tfa.requests.post")
    def test_start_fails_when_unreachable(self, mock_post):
        mock_post.side_effect = ConnectionError("no route")
        self.assertFalse(self._tfa().start())

    @patch("pyaarlo.tfa.requests.get")
    def test_get_returns_code(self, mock_get):
        mock_get.return_value = _response(
            200, {"meta": {"code": 200}, "data": {"code": "123456"}}
        )
        tfa = self._tfa()
        self.assertEqual(tfa.get(), "123456")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secret-token"})
        self.assertEqual(kwargs["params"], {"email": "arlo-tfa@example.com"})

    @patch("pyaarlo.tfa.requests.get")
    def test_get_times_out(self, mock_get):
        mock_get.return_value = _response(200, {"meta": {"code": 200}, "data": {}})
        self.assertIsNone(self._tfa(tfa_total_timeout=0.05).get())

    @patch("pyaarlo.tfa.requests.get")
    def test_get_survives_request_errors(self, mock_get):
        mock_get.side_effect = ConnectionError("flaky")
        self.assertIsNone(self._tfa(tfa_total_timeout=0.05).get())

    @patch("pyaarlo.tfa.requests.post")
    def test_stop_clears_and_swallows_errors(self, mock_post):
        tfa = self._tfa()
        tfa.stop()
        mock_post.assert_called_once()
        mock_post.side_effect = ConnectionError("gone")
        tfa.stop()


class TestWorker(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = _load_worker()

    def test_extract_code_plain_text(self):
        raw = (
            b"From: do_not_reply@arlo.com\r\nTo: a@b.com\r\nSubject: code\r\n"
            b"Content-Type: text/plain; charset=\"utf-8\"\r\n\r\n"
            b"Your code is:\r\n\r\n654321\r\n\r\nThanks\r\n"
        )
        self.assertEqual(self.worker.extract_code(raw), "654321")

    def test_extract_code_quoted_printable_html(self):
        raw = (
            b"From: do_not_reply@arlo.com\r\nTo: a@b.com\r\nSubject: code\r\n"
            b"Content-Type: text/html; charset=\"utf-8\"\r\n"
            b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
            b"<p>Your code is:</p>\r\n987654=20\r\n<p>Thanks</p>\r\n"
        )
        self.assertEqual(self.worker.extract_code(raw), "987654")

    def test_extract_code_sample_eml(self):
        with open(SAMPLE_EML_PATH, "rb") as f:
            self.assertEqual(self.worker.extract_code(f.read()), "123456")

    def test_extract_code_none_when_absent(self):
        raw = (
            b"From: do_not_reply@arlo.com\r\nTo: a@b.com\r\nSubject: hi\r\n"
            b"Content-Type: text/plain\r\n\r\nNothing to see here, not even 12345.\r\n"
        )
        self.assertIsNone(self.worker.extract_code(raw))

    def test_extract_code_ignores_mid_line_digits(self):
        raw = (
            b"From: do_not_reply@arlo.com\r\nTo: a@b.com\r\nSubject: hi\r\n"
            b"Content-Type: text/plain\r\n\r\nOrder 123456 has shipped today.\r\n"
        )
        self.assertIsNone(self.worker.extract_code(raw))

    def test_sender_allowed(self):
        self.assertTrue(self.worker.sender_allowed('"Arlo" <do_not_reply@arlo.com>'))
        self.assertTrue(self.worker.sender_allowed("do_not_reply@arlo.com"))
        self.assertTrue(self.worker.sender_allowed("DO_NOT_REPLY@ARLO.COM"))

    def test_sender_rejected(self):
        self.assertFalse(self.worker.sender_allowed("attacker@evil.com"))
        self.assertFalse(
            self.worker.sender_allowed('"do_not_reply@arlo.com" <attacker@evil.com>')
        )
        self.assertFalse(self.worker.sender_allowed(None))
        self.assertFalse(self.worker.sender_allowed(""))

    def test_kv_key(self):
        self.assertEqual(self.worker.kv_key(" Arlo-TFA@Example.COM "), "code:arlo-tfa@example.com")
        self.assertEqual(self.worker.kv_key(None), "code:")
