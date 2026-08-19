import unittest

from core.factory_v2.ui_automation.adb import AdbClient, CompletedCommand


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        return self.responses.pop(0)


class AdbForegroundTests(unittest.TestCase):
    def test_android15_falls_back_to_activity_top_resumed_activity(self):
        window_output = """\
  Window #8 Window{aa19647 u0 com.instagram.android/com.instagram.android.activity.MainTabActivity}:
    mOwnerUid=10209 showForAllUsers=false package=com.instagram.android appop=NONE
    mActivityRecord=ActivityRecord{ed5ecbd u0 com.instagram.android/.activity.MainTabActivity t6}
"""
        activity_output = """\
  * Task{ca8c60a #6 type=standard A=10209:com.instagram.android U=0 visible=true}
    topResumedActivity=ActivityRecord{ed5ecbd u0 com.instagram.android/.activity.MainTabActivity t6}
    Resumed: ActivityRecord{ed5ecbd u0 com.instagram.android/.activity.MainTabActivity t6}
  ResumedActivity: ActivityRecord{ed5ecbd u0 com.instagram.android/.activity.MainTabActivity t6}
  mCurrentFocus=Window{aa19647 u0 com.instagram.android/com.instagram.android.activity.MainTabActivity}
  mFocusedApp=ActivityRecord{ed5ecbd u0 com.instagram.android/.activity.MainTabActivity t6}
"""
        runner = FakeRunner([
            CompletedCommand(0, window_output, ""),
            CompletedCommand(0, activity_output, ""),
        ])
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        package, activity = adb.foreground()

        self.assertEqual("com.instagram.android", package)
        self.assertEqual(".activity.MainTabActivity", activity)
        self.assertEqual(
            ["adb", "-s", "emulator-5554", "shell", "dumpsys", "activity", "activities"],
            runner.calls[1][0],
        )

    def test_window_focus_format_still_uses_existing_fast_path(self):
        window_output = """\
  mCurrentFocus=Window{1 u0 com.instagram.android/com.instagram.android.activity.MainTabActivity}
"""
        runner = FakeRunner([CompletedCommand(0, window_output, "")])
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        package, activity = adb.foreground()

        self.assertEqual("com.instagram.android", package)
        self.assertEqual("com.instagram.android.activity.MainTabActivity", activity)
        self.assertEqual(1, len(runner.calls))


if __name__ == "__main__":
    unittest.main()
