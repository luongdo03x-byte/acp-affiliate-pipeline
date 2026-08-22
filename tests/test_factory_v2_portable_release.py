import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_MODULE = "core.factory_v2.portable_release"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None


class PortableReleaseModuleContractTests(unittest.TestCase):
    def test_portable_release_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_release module missing")


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_release module not implemented yet")
class PortableReleaseTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_release import CommandResult, GitHubReleaseTransport

        self.CommandResult = CommandResult
        self.GitHubReleaseTransport = GitHubReleaseTransport
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _runner(self, responses):
        calls = []

        def run(argv):
            key = tuple(argv)
            calls.append(key)
            response = responses.get(key)
            if response is None:
                return self.CommandResult(1, "", "unexpected command")
            if isinstance(response, list):
                if not response:
                    return self.CommandResult(1, "", "unexpected repeated command")
                return response.pop(0)
            return response

        return run, calls

    def test_assert_authenticated_normalizes_failure(self):
        runner, _ = self._runner({
            ("gh", "auth", "status", "--hostname", "github.com"):
                self.CommandResult(1, "", "token: ghp_should_not_escape"),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        with self.assertRaisesRegex(RuntimeError, "^GITHUB_AUTH_REQUIRED$") as ctx:
            transport.assert_authenticated()

        self.assertNotIn("ghp_should_not_escape", str(ctx.exception))

    def test_list_assets_filters_only_generation_assets(self):
        payload = json.dumps({
            "assets": [
                {"name": "acp-state-g000002.tar.gz", "size": 10},
                {"name": "notes.txt", "size": 1},
                {"name": "acp-state-g2.tar.gz", "size": 3},
                {"name": "acp-state-g000009.tar.gz", "size": 20},
            ]
        })
        runner, _ = self._runner({
            ("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets"):
                self.CommandResult(0, payload, ""),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        assets = transport.list_assets()

        self.assertEqual(
            ["acp-state-g000002.tar.gz", "acp-state-g000009.tar.gz"],
            [asset["name"] for asset in assets],
        )

    def test_ensure_release_creates_missing_release(self):
        view = ("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets")
        create = (
            "gh", "release", "create", "acp-portable-state", "--repo", "o/r",
            "--title", "ACP Portable State", "--notes", "Portable Account Factory state generations",
        )
        runner, calls = self._runner({
            view: self.CommandResult(1, "", "release not found"),
            create: self.CommandResult(0, "", ""),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        transport.ensure_release()

        self.assertEqual([view, create], calls)

    def test_upload_never_clobbers_existing_asset(self):
        archive = self.root / "acp-state-g000005.tar.gz"
        archive.write_bytes(b"payload")
        upload = (
            "gh", "release", "upload", "acp-portable-state", str(archive),
            "--repo", "o/r",
        )
        runner, calls = self._runner({upload: self.CommandResult(0, "", "")})
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        transport.upload(archive)

        self.assertEqual([upload], calls)
        self.assertNotIn("--clobber", calls[0])

    def test_download_generation_uses_exact_contract_name(self):
        destination = self.root / "downloads"
        destination.mkdir()
        exact = "acp-state-g000007.tar.gz"
        command = (
            "gh", "release", "download", "acp-portable-state",
            "--pattern", exact, "--dir", str(destination), "--repo", "o/r",
        )

        def runner(argv):
            self.assertEqual(command, tuple(argv))
            (destination / exact).write_bytes(b"state")
            return self.CommandResult(0, "", "")

        transport = self.GitHubReleaseTransport("o/r", runner=runner)
        path = transport.download_generation(7, destination)

        self.assertEqual(destination / exact, path)
        self.assertEqual(b"state", path.read_bytes())

    def test_verify_remote_asset_requires_exact_size_match(self):
        archive = self.root / "acp-state-g000005.tar.gz"
        archive.write_bytes(b"1234567")
        payload = json.dumps({
            "assets": [{"name": archive.name, "size": 8}],
        })
        runner, _ = self._runner({
            ("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets"):
                self.CommandResult(0, payload, ""),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        with self.assertRaisesRegex(RuntimeError, "^REMOTE_ASSET_VERIFICATION_FAILED$"):
            transport.verify_remote_asset(archive)

    def test_verify_remote_asset_passes_on_exact_size(self):
        archive = self.root / "acp-state-g000005.tar.gz"
        archive.write_bytes(b"1234567")
        payload = json.dumps({
            "assets": [{"name": archive.name, "size": 7}],
        })
        runner, _ = self._runner({
            ("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets"):
                self.CommandResult(0, payload, ""),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        transport.verify_remote_asset(archive)

    def test_prune_keep_latest_deletes_only_oldest_generation_assets(self):
        view = ("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets")
        payload = json.dumps({
            "assets": [
                *[
                    {"name": f"acp-state-g{generation:06d}.tar.gz", "size": generation}
                    for generation in range(1, 8)
                ],
                {"name": "notes.txt", "size": 1},
                {"name": "acp-state-g7.tar.gz", "size": 1},
            ]
        })
        delete1 = (
            "gh", "release", "delete-asset", "acp-portable-state",
            "acp-state-g000001.tar.gz", "--repo", "o/r", "--yes",
        )
        delete2 = (
            "gh", "release", "delete-asset", "acp-portable-state",
            "acp-state-g000002.tar.gz", "--repo", "o/r", "--yes",
        )
        runner, calls = self._runner({
            view: self.CommandResult(0, payload, ""),
            delete1: self.CommandResult(0, "", ""),
            delete2: self.CommandResult(0, "", ""),
        })
        transport = self.GitHubReleaseTransport("o/r", runner=runner)

        transport.prune_keep_latest(keep=5)

        self.assertEqual([view, delete1, delete2], calls)


if __name__ == "__main__":
    unittest.main()
