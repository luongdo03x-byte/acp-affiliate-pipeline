import unittest

from core.factory_v2.models import AccountStage as S
from core.factory_v2.state_machine import can_transition, safe_stage_after_transition


class FactoryV2StateMachineTests(unittest.TestCase):
    def test_happy_path_and_no_shortcut(self):
        self.assertTrue(can_transition(S.NEW, S.PROFILE_READY))
        self.assertTrue(can_transition(S.IG_CREATED, S.THREADS_READY_FOR_HUMAN))
        self.assertTrue(can_transition(S.THREADS_CREATED, S.ACP_CONNECTING))
        self.assertTrue(can_transition(S.ACP_CONNECTING, S.ACP_ACTIVE))
        self.assertFalse(can_transition(S.NEW, S.ACP_ACTIVE))

    def test_waiting_human_does_not_advance_safe_stage(self):
        self.assertEqual(S.IG_CREATED, safe_stage_after_transition(S.IG_CREATED, S.WAITING_HUMAN))
        self.assertEqual(S.THREADS_CREATED, safe_stage_after_transition(S.IG_CREATED, S.THREADS_CREATED))


if __name__ == "__main__":
    unittest.main()
