from pathlib import Path
import unittest


class PortableDocsCiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.workflow = (self.root / ".github" / "workflows" / "account-factory-ci.yml").read_text(encoding="utf-8")
        self.readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.runbook = (self.root / "docs" / "ACP_RUNBOOK.md").read_text(encoding="utf-8")

    def test_ci_tracks_portable_entrypoints_and_runs_all_portability_suites(self):
        for path in (
            '"manage.sh"',
            '"setup.sh"',
        ):
            self.assertIn(path, self.workflow)

        for module in (
            "tests.test_factory_v2_portable_state",
            "tests.test_factory_v2_portable_release",
            "tests.test_factory_v2_portable_doctor",
            "tests.test_factory_v2_portable_doctor_readiness",
            "tests.test_factory_v2_portable_resume",
            "tests.test_factory_v2_portable_cli",
            "tests.test_factory_v2_portable_cli_commands",
            "tests.test_factory_v2_manage_portable",
            "tests.test_factory_v2_portable_setup",
            "tests.test_factory_v2_portable_setup_prereq",
            "tests.test_factory_v2_portable_setup_rollback",
            "tests.test_factory_v2_portable_docs_ci",
        ):
            self.assertIn(module, self.workflow)

        self.assertIn("bash -n manage.sh", self.workflow)
        self.assertIn("bash -n setup.sh", self.workflow)

    def test_readme_and_runbook_show_supported_machine_switch_commands(self):
        required = (
            "./manage.sh handoff-out",
            "git clone -b feat/account-factory-android git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git",
            "cd acp-affiliate-pipeline",
            "./setup.sh",
            "git pull --ff-only",
        )
        for document in (self.readme, self.runbook):
            for command in required:
                self.assertIn(command, document)

    def test_docs_warn_about_plaintext_release_secrets_and_human_checkpoints(self):
        for document in (self.readme, self.runbook):
            self.assertIn("acp-portable-state", document)
            self.assertIn(".env.local", document)
            self.assertIn("plaintext", document.lower())
            self.assertIn("2FA", document)
            self.assertIn("passkey", document.lower())
            self.assertIn("OAuth consent", document)
            self.assertIn("Chrome Terms", document)


if __name__ == "__main__":
    unittest.main()
