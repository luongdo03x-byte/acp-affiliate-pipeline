import base64
import io
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.factory_v2.portable_crypto import decrypt_portable_bundle, encrypt_portable_bundle
from core.factory_v2.portable_state import (
    MachineState,
    build_bundle,
    validate_bundle,
    write_machine_state,
)


_KEY = base64.b64encode(b"\x33" * 32).decode("ascii")


class FakeTransport:
    def __init__(self, *, assets=None, downloads=None):
        self.assets = list(assets or [])
        self.downloads = dict(downloads or {})
        self.calls = []
        self.uploaded_bytes = None
        self.uploaded_name = None

    def assert_authenticated(self):
        self.calls.append(("auth",))

    def list_assets(self):
        self.calls.append(("list_assets",))
        return list(self.assets)

    def ensure_release(self):
        self.calls.append(("ensure_release",))

    def upload(self, path):
        path = Path(path)
        self.calls.append(("upload", path.name))
        self.uploaded_name = path.name
        self.uploaded_bytes = path.read_bytes()
        self.assets.append({"name": path.name, "size": path.stat().st_size})

    def verify_remote_asset(self, path):
        path = Path(path)
        self.calls.append(("verify", path.name))

    def prune_keep_latest(self, keep=5):
        self.calls.append(("prune", int(keep)))

    def download_generation(self, generation, destination):
        generation = int(generation)
        self.calls.append(("download", generation))
        source = Path(self.downloads[generation])
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copyfile(source, target)
        return target


class PortableCliEncryptionTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_cli import handoff_in, handoff_out

        self.handoff_in = handoff_in
        self.handoff_out = handoff_out
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _seed_base(self, name="source", marker="source"):
        base = self.root / name
        shared = base / "shared"
        var = shared / "var"
        avatars = shared / "avatars"
        var.mkdir(parents=True)
        avatars.mkdir(parents=True)
        (shared / ".env.local").write_text(
            "ACP_MASTER_KEY=fake-master-key\n"
            "THREADS_APP_SECRET=portable-provider-secret\n"
            f"ACP_DB={var / 'acp-live.db'}\n"
            f"ACP_AVATAR_DIR={avatars}\n",
            encoding="utf-8",
        )
        (shared / ".env.local").chmod(0o600)
        conn = sqlite3.connect(var / "acp-live.db")
        try:
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker(value) VALUES (?)", (marker,))
            conn.commit()
        finally:
            conn.close()
        write_machine_state(
            shared / "machine.json",
            MachineState(f"machine-{name}", 0, "ACTIVE"),
        )
        return base

    def test_handoff_out_requires_bundle_key_before_transport(self):
        base = self._seed_base()
        transport = FakeTransport()
        with patch.dict(os.environ, {"ACP_PORTABLE_BUNDLE_KEY": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "^PORTABLE_BUNDLE_KEY_REQUIRED$"):
                self.handoff_out(
                    base=base,
                    repo="o/r",
                    git_commit="abc123",
                    git_branch="feat/account-factory-android",
                    transport=transport,
                    out=io.StringIO(),
                )
        self.assertEqual([], transport.calls)

    def test_handoff_out_uploads_authenticated_ciphertext(self):
        base = self._seed_base()
        transport = FakeTransport()
        with patch.dict(os.environ, {"ACP_PORTABLE_BUNDLE_KEY": _KEY}, clear=False):
            generation = self.handoff_out(
                base=base,
                repo="o/r",
                git_commit="abc123",
                git_branch="feat/account-factory-android",
                transport=transport,
                out=io.StringIO(),
            )

        self.assertEqual(1, generation)
        self.assertEqual("acp-state-g000001.tar.gz", transport.uploaded_name)
        self.assertTrue(transport.uploaded_bytes.startswith(b"ACPPORT1"))
        self.assertNotIn(b"portable-provider-secret", transport.uploaded_bytes)

        encrypted = self.root / transport.uploaded_name
        encrypted.write_bytes(transport.uploaded_bytes)
        plain = self.root / "plain.tar.gz"
        decrypt_portable_bundle(encrypted, plain, _KEY)
        manifest = validate_bundle(plain, expected_generation=1)
        self.assertEqual(1, manifest["generation"])

    def test_handoff_in_decrypts_before_validate_and_restore(self):
        source = self._seed_base("bundle-source", marker="remote-encrypted")
        plain = build_bundle(
            snapshot_db=source / "shared" / "var" / "acp-live.db",
            env_path=source / "shared" / ".env.local",
            avatar_dir=source / "shared" / "avatars",
            output_dir=self.root / "plain",
            generation=1,
            source_machine_id="source-machine",
            source_git_commit="abc123",
            source_branch="feat/account-factory-android",
        )
        encrypted = self.root / "remote" / plain.name
        encrypt_portable_bundle(plain, encrypted, _KEY)
        transport = FakeTransport(
            assets=[{"name": encrypted.name, "size": encrypted.stat().st_size}],
            downloads={1: encrypted},
        )
        target = self.root / "target"

        with patch.dict(os.environ, {"ACP_PORTABLE_BUNDLE_KEY": _KEY}, clear=False):
            generation = self.handoff_in(
                base=target,
                repo="o/r",
                transport=transport,
                out=io.StringIO(),
                machine_id="target-machine",
            )

        self.assertEqual(1, generation)
        conn = sqlite3.connect(target / "shared" / "var" / "acp-live.db")
        try:
            self.assertEqual(
                "remote-encrypted",
                conn.execute("SELECT value FROM marker").fetchone()[0],
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
