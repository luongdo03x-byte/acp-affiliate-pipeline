import re
import unittest
from collections import Counter

from core.factory_v2.identity import (
    IdentityPools,
    generate_profiles,
    username_fallback_candidates,
)


class FactoryV2IdentityTests(unittest.TestCase):
    def test_default_batch_has_required_distribution(self):
        rows = generate_profiles(50, seed=17082026)
        self.assertEqual(50, len(rows))
        self.assertEqual(50, len({r.username for r in rows}))
        genders = Counter(r.gender_profile for r in rows)
        self.assertEqual({"female": 35, "male": 15}, dict(genders))
        niches = Counter(r.primary_niche for r in rows)
        self.assertEqual({
            "beauty": 9, "fashion": 9, "tech": 8,
            "home": 8, "fitness": 8, "food": 8,
        }, dict(niches))
        avatars = Counter(r.avatar_type for r in rows)
        self.assertEqual({"illustration": 30, "object": 20}, dict(avatars))

    def test_usernames_are_normalized_and_not_factory_sequences(self):
        rows = generate_profiles(50, seed=17082026)
        self.assertTrue(all(r.username == r.username.lower() for r in rows))
        self.assertTrue(all(" " not in r.username for r in rows))
        self.assertTrue(all(not r.username.startswith("acp") for r in rows))
        self.assertTrue(all(not r.username.startswith("user00") for r in rows))

    def test_collision_uses_another_natural_candidate_before_digits(self):
        pools = IdentityPools(
            surnames=("Nguyễn",),
            female_given=("Mai Anh",),
            male_given=("Mai Anh",),
        )
        rows = generate_profiles(2, seed=1, pools=pools)
        self.assertEqual(2, len({r.username for r in rows}))
        self.assertTrue(all(not any(ch.isdigit() for ch in r.username) for r in rows))

    def test_username_fallback_candidates_are_stable_bounded_and_safe(self):
        first = username_fallback_candidates("baongocd", "acc-1")
        second = username_fallback_candidates("baongocd", "acc-1")
        self.assertEqual(first, second)
        self.assertEqual(5, len(first))
        self.assertEqual(5, len(set(first)))
        self.assertNotIn("baongocd", first)
        self.assertTrue(all(len(value) <= 30 for value in first))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9._]+", value) for value in first))

    def test_username_fallback_candidates_change_with_account_id(self):
        self.assertNotEqual(
            username_fallback_candidates("baongocd", "acc-1"),
            username_fallback_candidates("baongocd", "acc-2"),
        )

    def test_username_fallback_candidates_never_exceed_five(self):
        self.assertEqual(
            5,
            len(username_fallback_candidates("baongocd", "acc-1", max_candidates=99)),
        )

    def test_username_fallback_candidates_require_stable_account_id(self):
        with self.assertRaisesRegex(ValueError, "account_id"):
            username_fallback_candidates("baongocd", "")


if __name__ == "__main__":
    unittest.main()
