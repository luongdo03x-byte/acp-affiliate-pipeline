import random
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2AvatarPoolTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    @staticmethod
    def _write_images(root: Path, count: int):
        result = []
        for index in range(1, count + 1):
            path = root / f"avatar_{index:03d}.jpg"
            path.write_bytes(f"avatar-{index}".encode("utf-8"))
            result.append(path.resolve())
        return result

    def test_create_batch_balances_avatar_usage_and_avoids_adjacent_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_root = Path(temp_dir)
            images = self._write_images(avatar_root, 3)
            service = FactoryService(
                self.repo,
                avatar_dir=avatar_root,
                avatar_rng=random.Random(17082026),
            )

            batch = service.create_batch("Avatar batch", count=8, seed=8)
            accounts = self.repo.list_accounts(batch["id"])
            assigned = [account["avatar_file"] for account in accounts]

            self.assertTrue(all(value is not None for value in assigned))
            self.assertTrue(all(Path(value).is_absolute() for value in assigned))
            self.assertEqual({str(path) for path in images}, set(assigned))
            self.assertTrue(all(left != right for left, right in zip(assigned, assigned[1:])))

            counts = Counter(assigned)
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_empty_avatar_directory_leaves_avatar_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = FactoryService(
                self.repo,
                avatar_dir=Path(temp_dir),
                avatar_rng=random.Random(1),
            )

            batch = service.create_batch("No avatars", count=2, seed=2)
            accounts = self.repo.list_accounts(batch["id"])

            self.assertEqual([None, None], [account["avatar_file"] for account in accounts])

    def test_single_account_accepts_explicit_avatar_inside_configured_pool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_root = Path(temp_dir)
            avatar = self._write_images(avatar_root, 1)[0]
            service = FactoryService(self.repo, avatar_dir=avatar_root)

            result = service.create_single_account(
                execution_target="AUTO_AVD",
                avatar_file=str(avatar),
            )

            self.assertEqual(str(avatar), result["account"]["avatar_file"])

    def test_single_account_rejects_absolute_avatar_outside_configured_pool(self):
        with tempfile.TemporaryDirectory() as avatar_dir, tempfile.TemporaryDirectory() as outside_dir:
            avatar_root = Path(avatar_dir)
            outside = Path(outside_dir) / "outside.jpg"
            outside.write_bytes(b"outside")
            service = FactoryService(self.repo, avatar_dir=avatar_root)

            with self.assertRaisesRegex(ValueError, "avatar_file"):
                service.create_single_account(
                    execution_target="AUTO_AVD",
                    avatar_file=str(outside.resolve()),
                )


if __name__ == "__main__":
    unittest.main()
