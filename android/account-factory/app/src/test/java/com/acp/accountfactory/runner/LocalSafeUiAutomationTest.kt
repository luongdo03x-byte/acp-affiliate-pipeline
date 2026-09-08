package com.acp.accountfactory.runner

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalSafeUiAutomationTest {
    private class FakeBridge(
        var packageName: String,
        var snapshot: List<LocalUiNode>,
    ) : LocalAccessibilityBridge {
        val clicks = mutableListOf<LocalUiSelector>()
        val longClicks = mutableListOf<LocalUiSelector>()
        val values = mutableListOf<String>()
        override fun foregroundPackage() = packageName
        override fun nodes() = snapshot
        override fun click(selector: LocalUiSelector): Boolean {
            clicks += selector
            return true
        }
        override fun longClick(selector: LocalUiSelector): Boolean {
            longClicks += selector
            return true
        }
        override fun setText(selector: LocalUiSelector, value: String): Boolean {
            values += value
            return true
        }
    }

    @Test
    fun passwordAlwaysStopsBeforeMutation() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(className = "android.widget.EditText", editable = true, password = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "sample"))

        assertEquals("waiting_human", result.status)
        assertEquals("PASSWORD_REQUIRED", result.screen)
        assertTrue(bridge.clicks.isEmpty())
        assertTrue(bridge.values.isEmpty())
    }

    @Test
    fun knownInitialSignupCanBeTapped() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(contentDescription = "Tạo tài khoản mới", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("running", result.status)
        assertEquals("IG_SIGNUP_ENTRY", result.screen)
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun finalSignupSubmitIsNeverTapped() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(text = "Đăng ký", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("waiting_human", result.status)
        assertEquals("IG_FINAL_SIGNUP_SUBMIT", result.screen)
        assertTrue(bridge.clicks.isEmpty())
    }

    @Test
    fun unknownUiNeverMutates() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.THREADS_PACKAGE,
            listOf(LocalUiNode(text = "Unexpected experiment")),
        )

        val result = LocalSafeUiAutomation(bridge).runThreads(emptyMap())

        assertEquals("needs_confirmation", result.status)
        assertFalse(bridge.clicks.isNotEmpty())
        assertTrue(bridge.values.isEmpty())
    }

    @Test
    fun existingInstagramHomeOpensAccountSwitcher() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(
                LocalUiNode(contentDescription = "Trang chủ"),
                LocalUiNode(
                    viewId = "com.instagram.android:id/profile_tab",
                    contentDescription = "Trang cá nhân",
                    longClickable = true,
                ),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(mapOf("username" to "new_user"))

        assertEquals("running", result.status)
        assertEquals("IG_HOME", result.screen)
        assertTrue(bridge.clicks.isEmpty())
        assertEquals(1, bridge.longClicks.size)
    }

    @Test
    fun observingExistingThreadsHomeDoesNotAdvanceFlow() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.THREADS_PACKAGE,
            listOf(
                LocalUiNode(contentDescription = "Home"),
                LocalUiNode(contentDescription = "Profile"),
            ),
        )

        val result = LocalSafeUiAutomation(bridge).observe("threads")

        assertEquals("needs_confirmation", result.status)
        assertEquals("THREADS_HOME", result.screen)
        assertEquals("CHECKPOINT_NOT_CONFIRMED", result.reason)
        assertTrue(bridge.clicks.isEmpty())
    }

    @Test
    fun instagramAccountSwitcherAddsAnotherAccount() {
        val bridge = FakeBridge(
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE,
            listOf(LocalUiNode(contentDescription = "Thêm tài khoản Instagram", clickable = true)),
        )

        val result = LocalSafeUiAutomation(bridge).runInstagram(emptyMap())

        assertEquals("running", result.status)
        assertEquals("IG_ACCOUNT_SWITCHER", result.screen)
        assertEquals(1, bridge.clicks.size)
        assertTrue(bridge.longClicks.isEmpty())
    }
}
