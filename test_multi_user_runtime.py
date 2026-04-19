import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodev.config import Config
from autodev.request_context import reset_request_context, set_request_context
from autodev.runtime_settings import get_provider_secret, redact_runtime_settings
from autodev.session_memory import get_workspace_dir, list_sessions, load_state, save_state


class MultiUserSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autodev_sessions_")
        self.sessions_patch = patch.object(Config, "SESSIONS_DIR", self.temp_dir)
        self.sessions_patch.start()

    def tearDown(self):
        self.sessions_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_same_session_id_isolated_per_user_scope(self):
        alice_tokens = set_request_context("alice", None, False)
        try:
            save_state("ses_shared", {"task": "alice task"})
            alice_workspace = get_workspace_dir("ses_shared")
        finally:
            reset_request_context(alice_tokens)

        bob_tokens = set_request_context("bob", None, False)
        try:
            save_state("ses_shared", {"task": "bob task"})
            bob_workspace = get_workspace_dir("ses_shared")
        finally:
            reset_request_context(bob_tokens)

        self.assertNotEqual(os.path.realpath(alice_workspace), os.path.realpath(bob_workspace))

        alice_tokens = set_request_context("alice", None, False)
        try:
            self.assertEqual(load_state("ses_shared")["task"], "alice task")
            self.assertEqual([item["id"] for item in list_sessions()], ["ses_shared"])
        finally:
            reset_request_context(alice_tokens)

        bob_tokens = set_request_context("bob", None, False)
        try:
            self.assertEqual(load_state("ses_shared")["task"], "bob task")
            self.assertEqual([item["id"] for item in list_sessions()], ["ses_shared"])
        finally:
            reset_request_context(bob_tokens)


class RuntimeSecretTests(unittest.TestCase):
    def test_provider_secret_uses_request_override_without_server_fallback(self):
        with patch.object(Config, "GOOGLE_API_KEY", "server-key"):
            override_tokens = set_request_context(
                "alice",
                {"secrets": {"geminiApiKey": "browser-key"}},
                False,
            )
            try:
                self.assertEqual(get_provider_secret("gemini"), "browser-key")
            finally:
                reset_request_context(override_tokens)

            no_override_tokens = set_request_context("alice", None, False)
            try:
                self.assertEqual(get_provider_secret("gemini"), "")
            finally:
                reset_request_context(no_override_tokens)

            local_tokens = set_request_context("alice", None, True)
            try:
                self.assertEqual(get_provider_secret("gemini"), "server-key")
            finally:
                reset_request_context(local_tokens)

    def test_redact_runtime_settings_clears_secret_fields(self):
        redacted = redact_runtime_settings({
            "secrets": {
                "geminiApiKey": "g",
                "groqApiKey": "x",
                "groqApiKey2": "y",
                "huggingFaceApiKey": "hf",
            },
            "local_models": {
                "localEndpointApiKey": "secret",
            },
            "customEndpoints": [
                {"id": "ep_1", "endpointUrl": "https://example.com", "apiKey": "hidden"},
            ],
        })
        self.assertEqual(redacted["secrets"]["geminiApiKey"], "")
        self.assertEqual(redacted["secrets"]["groqApiKey"], "")
        self.assertEqual(redacted["secrets"]["groqApiKey2"], "")
        self.assertEqual(redacted["secrets"]["huggingFaceApiKey"], "")
        self.assertEqual(redacted["local_models"]["localEndpointApiKey"], "")
        self.assertEqual(redacted["customEndpoints"][0]["apiKey"], "")


if __name__ == "__main__":
    unittest.main()
