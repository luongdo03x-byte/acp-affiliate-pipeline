import unittest

from core.factory_v2.resource_policy import (
    CapacityState,
    HostSample,
    classify_capacity,
    next_worker_target,
)


class FactoryV2ResourcePolicyTests(unittest.TestCase):
    def test_capacity_thresholds(self):
        self.assertEqual(CapacityState.GREEN, classify_capacity(HostSample(40, 8192, 0, 0, 1, 1)))
        self.assertEqual(CapacityState.YELLOW, classify_capacity(HostSample(70, 5000, 0, 0, 1, 1)))
        self.assertEqual(CapacityState.RED, classify_capacity(HostSample(90, 2500, 0, 0, 4, 4)))
        self.assertEqual(CapacityState.EMERGENCY, classify_capacity(HostSample(40, 1200, 0, 0, 1, 1)))

    def test_waiting_human_pressure_blocks_green_scale_up(self):
        self.assertEqual(5, next_worker_target(5, 2, CapacityState.GREEN, 2048))
        self.assertEqual(6, next_worker_target(5, 1, CapacityState.GREEN, 2048))

    def test_red_drains_at_most_one_and_preserves_waiting_human(self):
        self.assertEqual(4, next_worker_target(5, 2, CapacityState.RED, 2048))
        self.assertEqual(3, next_worker_target(3, 3, CapacityState.EMERGENCY, 2048))


if __name__ == "__main__":
    unittest.main()
