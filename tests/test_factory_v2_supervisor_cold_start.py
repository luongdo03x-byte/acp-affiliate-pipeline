import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.supervisor import WorkerSupervisor


class FakeMetrics:
    def __init__(self, sample):
        self.value = sample
        self.ram_total_mb = 14814

    def sample(self):
        return self.value


class FakeAvd:
    def __init__(self):
        self.started = []
        self.stopped = []

    def list_online_devices(self):
        return []

    def list_avds(self):
        return ["acp-worker-01"]

    def is_boot_completed(self, serial):
        return False

    def start(self, avd_name, port):
        self.started.append((avd_name, port))
        return type("P", (), {"pid": 4321})()

    def stop(self, serial):
        self.stopped.append(serial)


class FactoryV2SupervisorColdStartTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.avd = FakeAvd()
        self.service.create_batch(
            "Social-only pilot", count=1, seed=41, completion_mode="SOCIAL_ONLY"
        )

    def tearDown(self):
        self.conn.close()

    def supervisor(self, sample):
        return WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(sample),
            stability_seconds=0,
        )

    def test_social_only_cold_start_reuses_stale_worker_when_red_is_swap_only(self):
        self.repo.insert_worker({
            "id": "worker:acp-worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "ERROR",
            "draining": 1,
            "recovery_count": 286,
            "last_error": "error: could not connect to TCP port 5554: Connection refused",
        })
        sample = HostSample(
            cpu_percent=22.94,
            ram_available_mb=7427,
            swap_used_mb=7157,
            swap_in_rate=86.68,
            load_1m=2.96,
            load_5m=2.86,
        )

        decision = self.supervisor(sample).tick()

        self.assertEqual("START", decision.action)
        self.assertEqual(1, decision.target_workers)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)
        worker = self.repo.get_worker("worker:acp-worker-01")
        self.assertEqual("STARTING", worker["state"])
        self.assertEqual(0, worker["draining"])
        self.assertIsNone(worker["last_error"])

    def test_social_only_swap_pressure_does_not_drain_avd_on_tick_after_start(self):
        sample = HostSample(
            cpu_percent=22.94,
            ram_available_mb=7427,
            swap_used_mb=7157,
            swap_in_rate=86.68,
            load_1m=2.96,
            load_5m=2.86,
        )
        supervisor = self.supervisor(sample)

        first = supervisor.tick()
        second = supervisor.tick()

        self.assertEqual("START", first.action)
        self.assertEqual("HOLD", second.action)
        self.assertEqual(1, second.target_workers)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)
        self.assertEqual([], self.avd.stopped)
        worker = self.repo.get_worker("worker:acp-worker-01")
        self.assertEqual("STARTING", worker["state"])
        self.assertEqual(0, worker["draining"])

    def test_social_only_swap_pressure_keeps_single_ready_avd_for_next_account(self):
        self.repo.insert_worker({
            "id": "worker:acp-worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        sample = HostSample(
            cpu_percent=22.94,
            ram_available_mb=7427,
            swap_used_mb=7157,
            swap_in_rate=86.68,
            load_1m=2.96,
            load_5m=2.86,
        )

        decision = self.supervisor(sample).tick()

        self.assertEqual("HOLD", decision.action)
        self.assertEqual(1, decision.target_workers)
        self.assertEqual([], self.avd.stopped)
        self.assertEqual("READY", self.repo.get_worker("worker:acp-worker-01")["state"])

    def test_social_only_cold_start_allows_one_avd_with_swap_yellow_and_green_headroom(self):
        sample = HostSample(
            cpu_percent=25.0,
            ram_available_mb=8192,
            swap_used_mb=4096,
            swap_in_rate=10.0,
            load_1m=2.0,
            load_5m=2.0,
        )

        decision = self.supervisor(sample).tick()

        self.assertEqual("START", decision.action)
        self.assertEqual(1, decision.target_workers)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)

    def test_social_only_cold_start_stays_blocked_when_red_has_low_ram(self):
        sample = HostSample(
            cpu_percent=20.0,
            ram_available_mb=2500,
            swap_used_mb=7157,
            swap_in_rate=86.68,
            load_1m=2.0,
            load_5m=2.0,
        )

        decision = self.supervisor(sample).tick()

        self.assertEqual("HOLD", decision.action)
        self.assertEqual(0, decision.target_workers)
        self.assertEqual([], self.avd.started)


if __name__ == "__main__":
    unittest.main()
