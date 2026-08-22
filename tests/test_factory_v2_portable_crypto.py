import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path


_MODULE = "core.factory_v2.portable_crypto"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None
_KEY = base64.b64encode(b"\x11" * 32).decode("ascii")
_OTHER_KEY = base64.b64encode(b"\x22" * 32).decode("ascii")


class PortableCryptoContractTests(unittest.TestCase):
    def test_portable_crypto_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_crypto module missing")


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_crypto module not implemented yet")
class PortableCryptoTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_crypto import decrypt_portable_bundle, encrypt_portable_bundle

        self.decrypt_portable_bundle = decrypt_portable_bundle
        self.encrypt_portable_bundle = encrypt_portable_bundle
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_encrypted_bundle_hides_plaintext_and_round_trips(self):
        secret = b"THREADS_APP_SECRET=must-not-appear-in-release"
        source = self.root / "plain.tar.gz"
        encrypted = self.root / "acp-state-g000001.tar.gz"
        restored = self.root / "restored.tar.gz"
        source.write_bytes(b"header\n" + secret + b"\npayload")

        self.encrypt_portable_bundle(source, encrypted, _KEY)

        self.assertNotIn(secret, encrypted.read_bytes())
        self.assertNotEqual(source.read_bytes(), encrypted.read_bytes())
        self.decrypt_portable_bundle(encrypted, restored, _KEY)
        self.assertEqual(source.read_bytes(), restored.read_bytes())

    def test_wrong_or_invalid_key_fails_closed_without_plaintext(self):
        source = self.root / "plain.tar.gz"
        encrypted = self.root / "acp-state-g000001.tar.gz"
        restored = self.root / "restored.tar.gz"
        source.write_bytes(b"sensitive-state")
        self.encrypt_portable_bundle(source, encrypted, _KEY)

        with self.assertRaisesRegex(RuntimeError, r"^PORTABLE_BUNDLE_DECRYPT_FAILED$"):
            self.decrypt_portable_bundle(encrypted, restored, _OTHER_KEY)
        self.assertFalse(restored.exists())

        with self.assertRaisesRegex(RuntimeError, r"^PORTABLE_BUNDLE_KEY_INVALID$"):
            self.encrypt_portable_bundle(source, encrypted, "not-a-valid-key")


if __name__ == "__main__":
    unittest.main()
