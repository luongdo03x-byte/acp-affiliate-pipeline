import unittest
from collections import Counter

from core.factory_v2.identity import IdentityPools, generate_profiles


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


if __name__ == "__main__":
    unittest.main()
