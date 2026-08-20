import unittest
from types import SimpleNamespace

from core.factory_v2.identity import username_fallback_candidates
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow


class FakeDriver:
    def __init__(
        self,
        screens,
        available=(
            "username", "display_name", "bio", "signup_contact", "birth_date",
            "add_profile_photo", "avatar_skip", "continue", "sign_up",
        ),
        *,
        tap_statuses=None,
        set_statuses=None,
        wait_screens=None,
        node_texts=None,
    ):
        self.screens = list(screens)
        self.last = self.screens[-1] if self.screens else DetectedScreen("UNKNOWN", 0, ())
        self.available = set(available)
        self.mutations = []
        self.set_values = []
        self.opened = []
        self.tap_statuses = list(tap_statuses or [])
        self.set_statuses = list(set_statuses or [])
        self.wait_screens = list(wait_screens or [])
        self.node_texts = dict(node_texts or {})

    def detect_screen(self):
        if self.screens:
            self.last = self.screens.pop(0)
        return self.last

    def find(self, selector):
        if selector.semantic not in self.available:
            return None
        return SimpleNamespace(text=self.node_texts.get(selector.semantic, ""))

    def set_text(self, selector, value):
        self.mutations.append(("set_text", selector.semantic))
        self.set_values.append((selector.semantic, value))
        status = self.set_statuses.pop(0) if self.set_statuses else "completed"
        if status in {"completed", "noop"}:
            self.node_texts[selector.semantic] = value
        return ActionResult(status)

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        status = self.tap_statuses.pop(0) if self.tap_statuses else "completed"
        return ActionResult(status)

    def wait_for(self, expected_screens, timeout):
        if self.wait_screens:
            self.last = self.wait_screens.pop(0)
        return self.last

    def open_package(self, package):
        self.mutations.append(("open_package", package))
        self.opened.append(package)


class InstagramFlowTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "username": "sample_user",
            "display_name": "Sample User",
            "bio": "Sample bio",
            "signup_contact_type": "phone",
            "signup_contact": "+84901234567",
            "birth_date": "2000-05-20",
            "avatar_file": "avatars/sample.jpg",
            "password": "must-not-be-used",
            "otp": "000000",
        }

    def test_otp_stops_before_mutation(self):
        driver = FakeDriver([DetectedScreen("OTP_REQUIRED", 0.82, ("verify-marker",), True)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual("OTP_REQUIRED", result.screen)
        self.assertEqual([], driver.mutations)

    def test_contact_entry_sets_supplied_contact_then_continues(self):
        driver = FakeDriver([DetectedScreen("IG_CONTACT_ENTRY", 0.96, ("contact",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("signup_contact", "+84901234567")], driver.set_values)
        self.assertEqual(
            [("set_text", "signup_contact"), ("tap", "continue")],
            driver.mutations,
        )
        self.assertEqual("IG_CONTACT_ENTRY", result.last_safe_step)

    def test_contact_entry_without_supplied_contact_fails_closed(self):
        profile = dict(self.profile)
        profile.pop("signup_contact")
        driver = FakeDriver([DetectedScreen("IG_CONTACT_ENTRY", 0.96, ("contact",), False)])
        result = InstagramFlow(driver).run(profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("MISSING_SIGNUP_CONTACT", result.reason)
        self.assertEqual([], driver.mutations)

    def test_birthday_entry_sets_supplied_date_then_continues(self):
        driver = FakeDriver([DetectedScreen("IG_BIRTHDAY_ENTRY", 0.96, ("birthday",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("birth_date", "2000-05-20")], driver.set_values)
        self.assertEqual(
            [("set_text", "birth_date"), ("tap", "continue")],
            driver.mutations,
        )
        self.assertEqual("IG_BIRTHDAY_ENTRY", result.last_safe_step)

    def test_birthday_entry_without_supported_input_fails_closed(self):
        driver = FakeDriver(
            [DetectedScreen("IG_BIRTHDAY_ENTRY", 0.96, ("birthday",), False)],
            available=("continue",),
        )
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.mutations)

    def test_unknown_birthday_picker_never_mutates(self):
        driver = FakeDriver([DetectedScreen("UNKNOWN", 0.0, (), False)] * 3)
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.mutations)

    def test_avatar_setup_uses_known_add_profile_photo_selector_only(self):
        driver = FakeDriver([DetectedScreen("IG_AVATAR_SETUP", 0.96, ("avatar",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "add_profile_photo")], driver.mutations)
        self.assertEqual("IG_AVATAR_SETUP", result.last_safe_step)

    def test_avatar_setup_without_staged_avatar_skips(self):
        profile = dict(self.profile)
        profile.pop("avatar_file")
        driver = FakeDriver([DetectedScreen("IG_AVATAR_SETUP", 0.96, ("avatar",), False)])
        result = InstagramFlow(driver).run(profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "avatar_skip")], driver.mutations)
        self.assertEqual("IG_AVATAR_SETUP", result.last_safe_step)

    def test_requested_username_valid_taps_next_without_profile_update(self):
        driver = FakeDriver(
            [DetectedScreen("IG_USERNAME_ENTRY", 0.97, ("create_username",), False)],
            available=("username", "continue"),
            node_texts={"username": "dragon.3275826"},
            wait_screens=[DetectedScreen("IG_USERNAME_VALID", 0.99, ("valid",), False)],
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("running", result.status)
        self.assertEqual([("username", "sample_user")], driver.set_values)
        self.assertIsNone(result.profile_updates)
        self.assertIn(("tap", "continue"), driver.mutations)
        self.assertEqual("IG_USERNAME_ENTRY", result.last_safe_step)

    def test_unavailable_username_uses_first_valid_fallback_and_reports_update(self):
        driver = FakeDriver(
            [DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, ("unavailable",), False)],
            available=("username", "continue"),
            node_texts={"username": "sample_user"},
            wait_screens=[DetectedScreen("IG_USERNAME_VALID", 0.99, ("valid",), False)],
        )
        expected = username_fallback_candidates("sample_user", "acc-1")[0]
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("running", result.status)
        self.assertEqual(expected, driver.set_values[-1][1])
        self.assertEqual({"username": expected}, result.profile_updates)
        self.assertIn(("tap", "continue"), driver.mutations)

    def test_five_unavailable_fallbacks_stop_without_sixth_candidate(self):
        unavailable = DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, ("unavailable",), False)
        driver = FakeDriver(
            [unavailable],
            available=("username", "continue"),
            node_texts={"username": "sample_user"},
            wait_screens=[unavailable] * 5,
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("USERNAME_UNAVAILABLE", result.reason)
        self.assertEqual(5, len(driver.set_values))
        self.assertNotIn(("tap", "continue"), driver.mutations)

    def test_username_rate_limit_stops_before_next_candidate(self):
        driver = FakeDriver(
            [DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, (), False)],
            available=("username", "continue"),
            node_texts={"username": "sample_user"},
            wait_screens=[DetectedScreen("RATE_LIMITED", 0.99, ("rate",), False)],
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("retry_pending", result.status)
        self.assertEqual("RATE_LIMITED", result.reason)
        self.assertEqual(1, len(driver.set_values))

    def test_username_unknown_validation_fails_closed(self):
        driver = FakeDriver(
            [DetectedScreen("IG_USERNAME_ENTRY", 0.97, (), False)],
            available=("username", "continue"),
            node_texts={"username": "dragon.3275826"},
            wait_screens=[DetectedScreen("UNKNOWN", 0.0, (), False)],
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertNotIn(("tap", "continue"), driver.mutations)

    def test_username_validation_app_crash_reopens_once_then_rechecks(self):
        username_entry = DetectedScreen("IG_USERNAME_ENTRY", 0.97, (), False)
        driver = FakeDriver(
            [username_entry, username_entry],
            available=("username", "continue"),
            node_texts={"username": "dragon.3275826"},
            wait_screens=[
                DetectedScreen("APP_CRASH", 0.99, ("crash",), False),
                DetectedScreen("IG_USERNAME_VALID", 0.99, ("valid",), False),
            ],
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("running", result.status)
        self.assertEqual(["com.instagram.android"], driver.opened)
        self.assertIn(("tap", "continue"), driver.mutations)

    def test_username_validation_repeated_app_crash_reopens_only_once(self):
        username_entry = DetectedScreen("IG_USERNAME_ENTRY", 0.97, (), False)
        crash = DetectedScreen("APP_CRASH", 0.99, ("crash",), False)
        driver = FakeDriver(
            [username_entry, username_entry],
            available=("username", "continue"),
            node_texts={"username": "dragon.3275826"},
            wait_screens=[crash, crash],
        )
        result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("APP_CRASH", result.reason)
        self.assertEqual(["com.instagram.android"], driver.opened)

    def test_unavailable_username_without_account_id_fails_closed(self):
        driver = FakeDriver(
            [DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, (), False)],
            available=("username", "continue"),
            node_texts={"username": "sample_user"},
        )
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("MISSING_ACCOUNT_ID", result.reason)
        self.assertEqual([], driver.mutations)

    def test_profile_setup_sets_only_approved_fields(self):
        driver = FakeDriver([DetectedScreen("IG_PROFILE_SETUP", 0.96, ("profile",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("username", "sample_user"), ("display_name", "Sample User"), ("bio", "Sample bio")], driver.set_values)
        self.assertNotIn(("set_text", "password"), driver.mutations)

    def test_restart_on_profile_screen_does_not_replay_signup_entry(self):
        driver = FakeDriver([DetectedScreen("IG_PROFILE_SETUP", 0.96, ("profile",), False)])
        InstagramFlow(driver).run(self.profile)
        self.assertNotIn(("tap", "sign_up"), driver.mutations)

    def test_logged_in_home_routes_to_existing_session_instead_of_completing(self):
        driver = FakeDriver(
            [DetectedScreen("IG_HOME", 0.96, ("home",), False)],
            available=("profile",),
        )
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "profile")], driver.mutations)
        self.assertEqual("IG_EXISTING_SESSION", result.last_safe_step)

    def test_normal_action_stops_after_three_failed_attempts(self):
        driver = FakeDriver(
            [DetectedScreen("IG_SIGNUP_ENTRY", 0.94, ("signup",), False)],
            tap_statuses=["postcondition_failed", "postcondition_failed", "postcondition_failed", "completed"],
        )
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual(3, driver.mutations.count(("tap", "sign_up")))

    def test_unknown_retries_observation_without_mutation(self):
        unknown = DetectedScreen("UNKNOWN", 0.0, (), False)
        driver = FakeDriver([unknown, unknown, unknown])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_rate_limit_never_rapid_retries(self):
        driver = FakeDriver([DetectedScreen("RATE_LIMITED", 0.95, ("rate",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("retry_pending", result.status)
        self.assertEqual("RATE_LIMITED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_network_error_observation_is_bounded(self):
        network = DetectedScreen("NETWORK_ERROR", 0.95, ("network",), False)
        driver = FakeDriver([network, network, network, DetectedScreen("IG_HOME", 0.96, ("home",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("retry_pending", result.status)
        self.assertEqual("NETWORK_ERROR", result.reason)
        self.assertEqual([], driver.mutations)
        self.assertEqual(1, len(driver.screens))

    def test_account_disabled_is_terminal_without_mutation(self):
        driver = FakeDriver([DetectedScreen("ACCOUNT_DISABLED", 0.99, ("disabled",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("error", result.status)
        self.assertEqual("ACCOUNT_DISABLED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_resume_requires_known_successor_without_mutation(self):
        driver = FakeDriver([DetectedScreen("IG_HOME", 0.96, ("home",), False)])
        result = InstagramFlow(driver).observe_checkpoint()
        self.assertEqual("completed", result.status)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_does_not_resume_from_unknown(self):
        unknown = DetectedScreen("UNKNOWN", 0.0, (), False)
        driver = FakeDriver([unknown, unknown, unknown])
        result = InstagramFlow(driver).observe_checkpoint()
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.mutations)

    def test_app_crash_reopens_once_then_rechecks(self):
        driver = FakeDriver(
            [
                DetectedScreen("APP_CRASH", 0.99, ("crash",), False),
                DetectedScreen("IG_SIGNUP_ENTRY", 0.94, ("signup",), False),
            ],
            available=("sign_up",),
        )
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual(["com.instagram.android"], driver.opened)
        self.assertIn(("tap", "sign_up"), driver.mutations)

    def test_repeated_app_crash_reopens_only_once(self):
        crash = DetectedScreen("APP_CRASH", 0.99, ("crash",), False)
        driver = FakeDriver([crash, crash])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual(["com.instagram.android"], driver.opened)


if __name__ == "__main__":
    unittest.main()
