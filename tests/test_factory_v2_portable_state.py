import importlib
import importlib.util
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path


_MODULE = "core.factory_v2.portable_state"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None


class PortableStateModuleContractTests(unittest.TestCase):
    def test_portable_state_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_state module missing")


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_state module not implemented yet")
class PortableStateTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_state import MachineState, write_machine_state

        self.MachineState = MachineState
        self.write_machine_state = write_machine_state
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.machine_file = self.root / "machine.json"

    def test_generation_parser_accepts_only_contract_name(self):
        from core.factory_v2.portable_state import generation_from_asset

        self.assertEqual(42, generation_from_asset("acp-state-g000042.tar.gz"))
        self.assertIsNone(generation_from_asset("state-latest.tar.gz"))
        self.assertIsNone(generation_from_asset("acp-state-g42.tar.gz"))
        self.assertIsNone(generation_from_asset("acp-state-g000042.tar.gz.bak"))

    def test_next_generation_uses_remote_and_local_maximum(self):
        from core.factory_v2.portable_state import next_generation

        self.assertEqual(
            10,
            next_generation(
                [
                    "notes.txt",
                    "acp-state-g000004.tar.gz",
                    "acp-state-g000009.tar.gz",
                ],
                7,
            ),
        )
        self.assertEqual(12, next_generation([], 11))

    def test_machine_state_round_trips_without_secret_fields(self):
        from core.factory_v2.portable_state import load_machine_state

        state = self.MachineState("m1", 7, "ACTIVE")
        self.write_machine_state(self.machine_file, state)

        self.assertEqual(state, load_machine_state(self.machine_file))
        raw = json.loads(self.machine_file.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "machine_id": "m1",
                "last_imported_generation": 7,
                "ownership": "ACTIVE",
            },
            raw,
        )

    def test_handed_off_machine_cannot_start(self):
        from core.factory_v2.portable_state import require_active_ownership

        self.write_machine_state(
            self.machine_file,
            self.MachineState("m1", 7, "HANDED_OFF"),
        )

        with self.assertRaisesRegex(RuntimeError, "^MACHINE_HANDED_OFF$"):
            require_active_ownership(self.machine_file)

    def test_missing_machine_state_cannot_start(self):
        from core.factory_v2.portable_state import require_active_ownership

        with self.assertRaisesRegex(RuntimeError, "^MACHINE_HANDED_OFF$"):
            require_active_ownership(self.machine_file)

    def test_invalid_ownership_is_rejected_on_write(self):
        with self.assertRaisesRegex(ValueError, "^INVALID_MACHINE_OWNERSHIP$"):
            self.write_machine_state(
                self.machine_file,
                self.MachineState("m1", 7, "UNKNOWN"),
            )

    def _sqlite_api(self):
        module = importlib.import_module(_MODULE)
        self.assertTrue(
            hasattr(module, "snapshot_sqlite"),
            "snapshot_sqlite missing",
        )
        self.assertTrue(
            hasattr(module, "validate_sqlite"),
            "validate_sqlite missing",
        )
        return module.snapshot_sqlite, module.validate_sqlite

    def test_snapshot_sqlite_copies_committed_wal_state(self):
        snapshot_sqlite, validate_sqlite = self._sqlite_api()
        source = self.root / "source.db"
        destination = self.root / "snapshot.db"

        conn = sqlite3.connect(source)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO item(value) VALUES ('from-wal')")
            conn.commit()

            snapshot_sqlite(source, destination)
        finally:
            conn.close()

        validate_sqlite(destination)
        copied = sqlite3.connect(destination)
        try:
            self.assertEqual(
                "from-wal",
                copied.execute("SELECT value FROM item").fetchone()[0],
            )
        finally:
            copied.close()

    def test_validate_sqlite_rejects_corrupt_database(self):
        _, validate_sqlite = self._sqlite_api()
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"this is not a sqlite database")

        with self.assertRaisesRegex(RuntimeError, "^SQLITE_INTEGRITY_FAILED$"):
            validate_sqlite(corrupt)

    def test_validate_sqlite_rejects_foreign_key_violation(self):
        _, validate_sqlite = self._sqlite_api()
        invalid = self.root / "foreign-key-invalid.db"
        conn = sqlite3.connect(invalid)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE child ("
                "id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id))"
            )
            conn.execute("INSERT INTO child(id, parent_id) VALUES (1, 999)")
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(RuntimeError, "^SQLITE_INTEGRITY_FAILED$"):
            validate_sqlite(invalid)

    def _bundle_api(self):
        module = importlib.import_module(_MODULE)
        for name in (
            "build_bundle",
            "validate_bundle",
            "restore_bundle",
            "normalize_portable_env",
        ):
            self.assertTrue(hasattr(module, name), f"{name} missing")
        return (
            module.build_bundle,
            module.validate_bundle,
            module.restore_bundle,
            module.normalize_portable_env,
        )

    def _make_bundle_inputs(self):
        snapshot = self.root / "snapshot.db"
        conn = sqlite3.connect(snapshot)
        try:
            conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO item(value) VALUES ('portable-row')")
            conn.commit()
        finally:
            conn.close()

        env_path = self.root / "source.env"
        env_path.write_text(
            "ACP_MASTER_KEY=unchanged-secret\n"
            "THREADS_APP_SECRET=provider-secret\n"
            "ACP_DB=/home/source/Downloads/ACP/shared/var/acp-live.db\n"
            "ACP_AVATAR_DIR=/home/source/Downloads/ACP/shared/avatars\n"
            "OTHER_PATH=/must/not/change\n",
            encoding="utf-8",
        )
        avatars = self.root / "avatars"
        avatars.mkdir()
        (avatars / "avatar.jpg").write_bytes(b"avatar-bytes")
        output = self.root / "bundles"
        return snapshot, env_path, avatars, output

    def _build_valid_bundle(self, generation=5):
        build_bundle, _, _, _ = self._bundle_api()
        snapshot, env_path, avatars, output = self._make_bundle_inputs()
        return build_bundle(
            snapshot_db=snapshot,
            env_path=env_path,
            avatar_dir=avatars,
            output_dir=output,
            generation=generation,
            source_machine_id="weekday-m1",
            source_git_commit="abc123",
            source_branch="feat/account-factory-android",
        )

    def test_bundle_round_trip_preserves_state_and_secret_bytes(self):
        _, validate_bundle, restore_bundle, _ = self._bundle_api()
        archive = self._build_valid_bundle(generation=5)

        self.assertEqual("acp-state-g000005.tar.gz", archive.name)
        self.assertEqual(0o600, archive.stat().st_mode & 0o777)
        manifest = validate_bundle(archive, expected_generation=5)
        self.assertEqual(1, manifest["format_version"])
        self.assertEqual(5, manifest["generation"])
        self.assertEqual("READY_FOR_IMPORT", manifest["handoff_state"])

        target = self.root / "target-base"
        restore_bundle(archive, base=target, expected_generation=5)

        restored_env = target / "shared" / ".env.local"
        self.assertEqual(0o600, restored_env.stat().st_mode & 0o777)
        self.assertIn(
            "ACP_MASTER_KEY=unchanged-secret\n",
            restored_env.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            b"avatar-bytes",
            (target / "shared" / "avatars" / "avatar.jpg").read_bytes(),
        )
        conn = sqlite3.connect(target / "shared" / "var" / "acp-live.db")
        try:
            self.assertEqual(
                "portable-row",
                conn.execute("SELECT value FROM item").fetchone()[0],
            )
        finally:
            conn.close()

    def test_validate_bundle_rejects_traversal_absolute_unknown_and_link_members(self):
        _, validate_bundle, _, _ = self._bundle_api()
        cases = [
            ("../escape", tarfile.REGTYPE, "UNSAFE_BUNDLE_PATH"),
            ("/tmp/escape", tarfile.REGTYPE, "UNSAFE_BUNDLE_PATH"),
            ("state/evil.txt", tarfile.REGTYPE, "BUNDLE_LAYOUT_INVALID"),
            ("state/shared/avatars/link", tarfile.SYMTYPE, "UNSAFE_BUNDLE_MEMBER"),
        ]

        for index, (name, member_type, code) in enumerate(cases):
            with self.subTest(name=name):
                archive = self.root / f"unsafe-{index}.tar.gz"
                with tarfile.open(archive, "w:gz") as tf:
                    info = tarfile.TarInfo(name)
                    info.type = member_type
                    if member_type == tarfile.SYMTYPE:
                        info.linkname = "../../outside"
                        info.size = 0
                        tf.addfile(info)
                    else:
                        payload = b"x"
                        info.size = len(payload)
                        import io
                        tf.addfile(info, io.BytesIO(payload))

                with self.assertRaisesRegex(RuntimeError, f"^{code}$"):
                    validate_bundle(archive, expected_generation=1)

    def test_validate_bundle_rejects_generation_mismatch(self):
        _, validate_bundle, _, _ = self._bundle_api()
        archive = self._build_valid_bundle(generation=5)

        with self.assertRaisesRegex(RuntimeError, "^BUNDLE_GENERATION_MISMATCH$"):
            validate_bundle(archive, expected_generation=6)

    def test_validate_bundle_rejects_checksum_mismatch(self):
        _, validate_bundle, _, _ = self._bundle_api()
        archive = self._build_valid_bundle(generation=5)
        staging = self.root / "tamper"
        staging.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(staging)
        (staging / "state" / "shared" / ".env.local").write_text(
            "ACP_MASTER_KEY=tampered\n",
            encoding="utf-8",
        )
        tampered = self.root / "tampered.tar.gz"
        with tarfile.open(tampered, "w:gz") as tf:
            tf.add(staging / "state", arcname="state")

        with self.assertRaisesRegex(RuntimeError, "^BUNDLE_CHECKSUM_MISMATCH$"):
            validate_bundle(tampered, expected_generation=5)

    def test_normalize_portable_env_rewrites_only_machine_local_paths(self):
        _, _, _, normalize_portable_env = self._bundle_api()
        env_path = self.root / "normalize.env"
        env_path.write_text(
            "ACP_MASTER_KEY=unchanged-secret\n"
            "THREADS_APP_SECRET=provider-secret\n"
            "ACP_DB=/home/source/Downloads/ACP/shared/var/acp-live.db\n"
            "ACP_AVATAR_DIR=/home/source/Downloads/ACP/shared/avatars\n"
            "OTHER_PATH=/must/not/change\n",
            encoding="utf-8",
        )
        base = self.root / "weekend-acp"

        normalize_portable_env(env_path, base)

        self.assertEqual(
            "ACP_MASTER_KEY=unchanged-secret\n"
            "THREADS_APP_SECRET=provider-secret\n"
            f"ACP_DB={base}/shared/var/acp-live.db\n"
            f"ACP_AVATAR_DIR={base}/shared/avatars\n"
            "OTHER_PATH=/must/not/change\n",
            env_path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
