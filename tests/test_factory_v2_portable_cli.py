import importlib.util
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.factory_v2.portable_state import (
    MachineState,
    build_bundle,
    load_machine_state,
    write_machine_state,
)


_MODULE = "core.factory_v2.portable_cli"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None


class PortableCliModuleContractTests(unittest.TestCase):
    def test_portable_cli_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_cli module missing")


class FakeTransport:
    def __init__(self, *, assets=None, downloads=None, fail_verify=False):
        self.assets = list(assets or [])
        self.downloads = dict(downloads or {})
        self.fail_verify = fail_verify
        self.calls = []

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
        self.assets.append({"name": path.name, "size": path.stat().st_size})

    def verify_remote_asset(self, path):
        path = Path(path)
        self.calls.append(("verify", path.name))
        if self.fail_verify:
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED")
        matches = [a for a in self.assets if a.get("name") == path.name]
        if len(matches) != 1 or int(matches[0].get("size", -1)) != path.stat().st_size:
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED")

    def download_generation(self, generation, destination):
        generation = int(generation)
        self.calls.append(("download", generation))
        source = Path(self.downloads[generation])
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copyfile(source, target)
        return target

    def prune_keep_latest(self, keep=5):
        self.calls.append(("prune", int(keep)))


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_cli module not implemented yet")
class PortableCliTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_cli import handoff_in, handoff_out

        self.handoff_in = handoff_in
        self.handoff_out = handoff_out
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _seed_base(self, name="ACP", *, generation=0, ownership="ACTIVE", marker="source"):
        base = self.root / name
        shared = base / "shared"
        var = shared / "var"
        avatars = shared / "avatars"
        var.mkdir(parents=True)
        avatars.mkdir(parents=True)
        env_path = shared / ".env.local"
        env_path.write_text(
            "ACP_MASTER_KEY=portable-test-key\n"
            "THREADS_APP_SECRET=portable-provider-secret\n"
            f"ACP_DB={var / 'acp-live.db'}\n"
            f"ACP_AVATAR_DIR={avatars}\n",
            encoding="utf-8",
        )
        env_path.chmod(0o600)
        db_path = var / "acp-live.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker(value) VALUES (?)", (marker,))
            conn.commit()
        finally:
            conn.close()
        (avatars / "avatar.txt").write_text(f"avatar-{marker}", encoding="utf-8")
        write_machine_state(
            shared / "machine.json",
            MachineState(f"machine-{name}", generation, ownership),
        )
        return base

    def _build_remote_bundle(self, generation, marker):
        source = self._seed_base(f"bundle-src-{generation}", marker=marker)
        return build_bundle(
            snapshot_db=source / "shared" / "var" / "acp-live.db",
            env_path=source / "shared" / ".env.local",
            avatar_dir=source / "shared" / "avatars",
            output_dir=self.root / "remote-assets",
            generation=generation,
            source_machine_id=f"source-{generation}",
            source_git_commit=f"commit-{generation}",
            source_branch="feat/account-factory-android",
        )

    @staticmethod
    def _read_marker(base):
        conn = sqlite3.connect(Path(base) / "shared" / "var" / "acp-live.db")
        try:
            return conn.execute("SELECT value FROM marker").fetchone()[0]
        finally:
            conn.close()

    def test_handoff_out_marks_handed_off_only_after_verified_upload(self):
        base = self._seed_base(generation=0, ownership="ACTIVE")
        transport = FakeTransport()
        output = io.StringIO()

        generation = self.handoff_out(
            base=base,
            repo="o/r",
            git_commit="abc123",
            git_branch="feat/account-factory-android",
            transport=transport,
            out=output,
        )

        self.assertEqual(1, generation)
        state = load_machine_state(base / "shared" / "machine.json")
        self.assertEqual("HANDED_OFF", state.ownership)
        self.assertEqual(1, state.last_imported_generation)
        self.assertEqual(
            [("auth",), ("list_assets",), ("ensure_release",),
             ("upload", "acp-state-g000001.tar.gz"),
             ("verify", "acp-state-g000001.tar.gz"), ("prune", 5)],
            transport.calls,
        )
        self.assertEqual("HANDOFF_OK generation=1\n", output.getvalue())

    def test_handoff_out_verify_failure_leaves_source_active(self):
        base = self._seed_base(generation=3, ownership="ACTIVE")
        transport = FakeTransport(
            assets=[{"name": "acp-state-g000003.tar.gz", "size": 1}],
            fail_verify=True,
        )
        output = io.StringIO()

        with self.assertRaisesRegex(RuntimeError, "^REMOTE_ASSET_VERIFICATION_FAILED$"):
            self.handoff_out(
                base=base,
                repo="o/r",
                git_commit="abc123",
                git_branch="feat/account-factory-android",
                transport=transport,
                out=output,
            )

        state = load_machine_state(base / "shared" / "machine.json")
        self.assertEqual("ACTIVE", state.ownership)
        self.assertEqual(3, state.last_imported_generation)
        self.assertNotIn("HANDOFF_OK", output.getvalue())
        self.assertNotIn(("prune", 5), transport.calls)

    def test_handoff_in_selects_highest_generation_and_claims_active(self):
        g4 = self._build_remote_bundle(4, "remote-four")
        g5 = self._build_remote_bundle(5, "remote-five")
        target = self.root / "weekend-ACP"
        transport = FakeTransport(
            assets=[
                {"name": g4.name, "size": g4.stat().st_size},
                {"name": "notes.txt", "size": 1},
                {"name": g5.name, "size": g5.stat().st_size},
            ],
            downloads={4: g4, 5: g5},
        )
        output = io.StringIO()

        generation = self.handoff_in(
            base=target,
            repo="o/r",
            transport=transport,
            out=output,
            machine_id="weekend-machine",
        )

        self.assertEqual(5, generation)
        self.assertEqual("remote-five", self._read_marker(target))
        state = load_machine_state(target / "shared" / "machine.json")
        self.assertEqual(MachineState("weekend-machine", 5, "ACTIVE"), state)
        self.assertEqual([("auth",), ("list_assets",), ("download", 5)], transport.calls)
        self.assertEqual("IMPORT_OK generation=5\n", output.getvalue())
        env_text = (target / "shared" / ".env.local").read_text(encoding="utf-8")
        self.assertIn("ACP_MASTER_KEY=portable-test-key\n", env_text)
        self.assertIn(f"ACP_DB={target}/shared/var/acp-live.db\n", env_text)

    def test_handoff_in_refuses_remote_downgrade_before_live_db_change(self):
        target = self._seed_base("target", generation=6, ownership="ACTIVE", marker="keep-live")
        g5 = self._build_remote_bundle(5, "older-remote")
        transport = FakeTransport(
            assets=[{"name": g5.name, "size": g5.stat().st_size}],
            downloads={5: g5},
        )
        output = io.StringIO()

        with self.assertRaisesRegex(RuntimeError, "^REMOTE_STATE_OLDER_THAN_LOCAL$"):
            self.handoff_in(
                base=target,
                repo="o/r",
                transport=transport,
                out=output,
                machine_id="target-machine",
            )

        self.assertEqual("keep-live", self._read_marker(target))
        state = load_machine_state(target / "shared" / "machine.json")
        self.assertEqual(6, state.last_imported_generation)
        self.assertEqual("ACTIVE", state.ownership)
        self.assertNotIn(("download", 5), transport.calls)
        self.assertEqual("", output.getvalue())

    def test_handoff_in_same_active_generation_skips_restore(self):
        target = self._seed_base("same", generation=8, ownership="ACTIVE", marker="keep-same")
        transport = FakeTransport(
            assets=[{"name": "acp-state-g000008.tar.gz", "size": 123}],
        )
        output = io.StringIO()

        generation = self.handoff_in(
            base=target,
            repo="o/r",
            transport=transport,
            out=output,
            machine_id="machine-same",
        )

        self.assertEqual(8, generation)
        self.assertEqual("keep-same", self._read_marker(target))
        self.assertNotIn(("download", 8), transport.calls)
        self.assertEqual("IMPORT_OK generation=8 already-current\n", output.getvalue())


if __name__ == "__main__":
    unittest.main()
