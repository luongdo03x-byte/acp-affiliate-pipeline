import unittest

from core.factory_v2.ui_automation.flow_result import FlowResult
from core.factory_v2.worker_protocol import WorkerCommand
from workers.account_factory_worker import WorkerAgent
from tests.test_factory_v2_avd_worker_agent import FakeAdbClient, FakeAvd, FakeBrowserLoginFlow
from tests.test_factory_v2_runtime_remote import (
    FakeGateway,
    FakeRepo,
    FakeService,
    TestRuntime,
    account,
    job,
)


class ObserveProfileFlow:
    def __init__(self):
        self.profiles = []
        self.driver = type("Driver", (), {"open_package": lambda self, package: None})()

    def run(self, profile, **kwargs):
        return FlowResult("running", "THREADS_ONBOARDING")

    def observe_checkpoint(self, profile):
        self.profiles.append(dict(profile))
        return FlowResult("completed", "THREADS_HOME")


class ThreadsCheckpointIdentityPlumbingTests(unittest.TestCase):
    def test_runtime_sends_current_profile_when_observing_threads_checkpoint(self):
        acc = account("WAITING_HUMAN")
        acc["username"] = "myduyenn681999"
        repo = FakeRepo(acc)
        repo.checkpoint = {"id": "cp-1", "type": "THREADS_POSTCHECK", "status": "OPEN"}
        service = FakeService(repo)
        gateway = FakeGateway([
            {"ok": True, "status": "waiting_human", "result": {"screen": "THREADS_HOME"}}
        ])
        runtime = TestRuntime(repo, service, gateway)

        runtime._drive_job(job("OBSERVE_CHECKPOINT"))

        self.assertEqual("OBSERVE_CHECKPOINT", gateway.commands[0][0])
        self.assertEqual("threads", gateway.commands[0][1]["flow"])
        self.assertEqual(
            "myduyenn681999",
            gateway.commands[0][1]["profile"]["username"],
        )

    def test_worker_passes_sanitized_profile_to_threads_checkpoint_observer(self):
        threads = ObserveProfileFlow()
        instagram = ObserveProfileFlow()
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=FakeAvd(),
            adb_client=FakeAdbClient(),
            instagram_flow=instagram,
            threads_flow=threads,
            browser_login_flow=FakeBrowserLoginFlow(),
        )

        response = agent.execute(WorkerCommand(
            "observe-threads",
            "OBSERVE_CHECKPOINT",
            "acc-1",
            {
                "flow": "threads",
                "profile": {
                    "username": "myduyenn681999",
                    "display_name": "My Duyen",
                    "bio": "sample",
                    "password": "must-not-pass",
                    "otp": "123456",
                },
            },
        ))

        self.assertEqual("completed", response["status"])
        self.assertEqual("myduyenn681999", threads.profiles[0]["username"])
        self.assertNotIn("password", threads.profiles[0])
        self.assertNotIn("otp", threads.profiles[0])


if __name__ == "__main__":
    unittest.main()
