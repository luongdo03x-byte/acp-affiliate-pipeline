from pathlib import Path
import re
import unittest


class ManageMigrateReleaseImportPathTests(unittest.TestCase):
    def test_migrate_release_uses_release_directory_and_core_namespace(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "manage.sh").read_text(encoding="utf-8")

        match = re.search(
            r'(?ms)^migrate_release\(\) \{\n(.*?)^\}\n\nswitch_active\(\)',
            text,
        )
        self.assertIsNotNone(match, "migrate_release() missing")

        block = match.group(1)

        self.assertIn('cd "$new"', block)
        self.assertIn("from core.db import init_db", block)

        self.assertNotIn('cd "$parent"', block)
        self.assertNotIn("from acp.core.db import init_db", block)


if __name__ == "__main__":
    unittest.main()
