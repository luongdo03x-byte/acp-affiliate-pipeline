package com.acp.accountfactory.runner

import com.acp.accountfactory.network.RunnerCommandDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class LocalDeviceActionsTest {
    private class FakePlatform : LocalPlatform {
        val packages = mutableListOf<String>()
        val urls = mutableListOf<String>()
        override fun openPackage(packageName: String): Boolean {
            packages += packageName
            return true
        }
        override fun openUrl(url: String): Boolean {
            urls += url
            return true
        }
    }

    private class FakeClipboard : LocalClipboard {
        var value: String? = null
        override fun putText(text: String) { value = text }
    }

    private class FakeBridge : LocalAccessibilityBridge {
        override fun foregroundPackage() = LocalSafeUiAutomation.INSTAGRAM_PACKAGE
        override fun nodes() = listOf(LocalUiNode(text = "Unexpected transition frame"))
        override fun click(selector: LocalUiSelector) = false
        override fun longClick(selector: LocalUiSelector) = false
        override fun tapAt(x: Int, y: Int) = false
        override fun dismissKeyboard() = false
        override fun setText(selector: LocalUiSelector, value: String) = false
    }

    private class SwitchingBridge : LocalAccessibilityBridge {
        private var screen = 0
        override fun foregroundPackage() = LocalSafeUiAutomation.INSTAGRAM_PACKAGE
        override fun nodes() = when (screen) {
            0 -> listOf(
                LocalUiNode(contentDescription = "Home"),
                LocalUiNode(
                    viewId = "com.instagram.android:id/profile_tab",
                    contentDescription = "Profile",
                    longClickable = true,
                ),
            )
            1 -> listOf(LocalUiNode(text = "Add Instagram account", clickable = true))
            else -> listOf(LocalUiNode(text = "Create new account", clickable = true))
        }
        override fun click(selector: LocalUiSelector): Boolean {
            screen += 1
            return true
        }
        override fun longClick(selector: LocalUiSelector): Boolean {
            screen += 1
            return true
        }
        override fun tapAt(x: Int, y: Int) = false
        override fun dismissKeyboard() = false
        override fun setText(selector: LocalUiSelector, value: String) = false
    }

    private fun command(action: String, payload: Map<String, String?> = emptyMap()) = RunnerCommandDto(
        id = "c1",
        jobId = "j1",
        accountId = "a1",
        action = action,
        payload = payload,
        createdAt = null,
    )

    @Test
    fun openPackageOnlyAcceptsOfficialAllowlist() {
        val platform = FakePlatform()
        val actions = LocalDeviceActions(platform, FakeClipboard(), ForegroundObservationStore())

        val result = actions.execute(command("OPEN_PACKAGE", mapOf("package" to "com.example.other")))

        assertEquals("FAILED", result.status)
        assertEquals("PACKAGE_NOT_ALLOWED", result.result["error_code"])
        assertEquals(emptyList<String>(), platform.packages)
    }

    @Test
    fun observeForegroundReportsPackageWithoutWorkflowStage() {
        val store = ForegroundObservationStore()
        store.update("com.instagram.android", "MainActivity", 123L)
        val actions = LocalDeviceActions(FakePlatform(), FakeClipboard(), store)

        val result = actions.execute(command("OBSERVE_FOREGROUND"))

        assertEquals("COMPLETED", result.status)
        assertEquals("com.instagram.android", result.result["package"])
        assertFalse(result.result.containsKey("stage"))
    }

    @Test
    fun prepareTextRejectsSensitivePayloadKeys() {
        val actions = LocalDeviceActions(FakePlatform(), FakeClipboard(), ForegroundObservationStore())
        val result = actions.execute(command("PREPARE_TEXT", mapOf("password" to "secret")))
        assertEquals("FAILED", result.status)
        assertEquals("SENSITIVE_PAYLOAD", result.result["error_code"])
    }

    @Test
    fun openUrlAcceptsHttpsOnly() {
        val platform = FakePlatform()
        val actions = LocalDeviceActions(platform, FakeClipboard(), ForegroundObservationStore())
        val bad = actions.execute(command("OPEN_URL", mapOf("url" to "javascript:alert(1)")))
        val good = actions.execute(command("OPEN_URL", mapOf("url" to "https://threads.example/oauth")))
        assertEquals("FAILED", bad.status)
        assertEquals("COMPLETED", good.status)
        assertEquals(listOf("https://threads.example/oauth"), platform.urls)
    }

    @Test
    fun transientUnknownUiGetsBoundedSettlingRetries() {
        val automation = LocalSafeUiAutomation(FakeBridge())
        val actions = LocalDeviceActions(
            FakePlatform(),
            FakeClipboard(),
            ForegroundObservationStore(),
            automationProvider = { automation },
        )

        repeat(5) {
            val result = actions.execute(command("AUTOMATE_INSTAGRAM"))
            assertEquals("running", result.result["flow_status"])
            assertEquals("UI_SETTLING", result.result["reason"])
        }
        val exhausted = actions.execute(command("AUTOMATE_INSTAGRAM"))
        assertEquals("needs_confirmation", exhausted.result["flow_status"])
        assertEquals("UI_CHANGED", exhausted.result["reason"])
    }

    @Test
    fun accountSwitcherNavigationPerformsOnlyOneMutationPerCommand() {
        val bridge = SwitchingBridge()
        val actions = LocalDeviceActions(
            FakePlatform(),
            FakeClipboard(),
            ForegroundObservationStore(),
            automationProvider = { LocalSafeUiAutomation(bridge) },
            uiTransitionWait = {},
        )

        val result = actions.execute(command("AUTOMATE_INSTAGRAM"))

        assertEquals("running", result.result["flow_status"])
        assertEquals("IG_HOME", result.result["screen"])
    }

    private class TesterInviteBridge : LocalAccessibilityBridge {
        val clicks = mutableListOf<LocalUiSelector>()
        override fun foregroundPackage() = LocalSafeUiAutomation.THREADS_PACKAGE
        override fun nodes() = listOf(
            LocalUiNode(text = "Lời mời"),
            LocalUiNode(text = "Chấp nhận", clickable = true),
        )
        override fun click(selector: LocalUiSelector): Boolean {
            clicks += selector
            return true
        }
        override fun longClick(selector: LocalUiSelector) = false
        override fun tapAt(x: Int, y: Int) = false
        override fun dismissKeyboard() = false
        override fun setText(selector: LocalUiSelector, value: String) = false
    }

    private class ConsentBridge : LocalAccessibilityBridge {
        val clicks = mutableListOf<LocalUiSelector>()
        override fun foregroundPackage() = "com.android.chrome"
        override fun nodes() = listOf(
            LocalUiNode(text = "threads_content_publish"),
            LocalUiNode(text = "Cho phép", clickable = true),
        )
        override fun click(selector: LocalUiSelector): Boolean {
            clicks += selector
            return true
        }
        override fun longClick(selector: LocalUiSelector) = false
        override fun tapAt(x: Int, y: Int) = false
        override fun dismissKeyboard() = false
        override fun setText(selector: LocalUiSelector, value: String) = false
    }

    @Test
    fun acceptThreadsTesterChayLuongChapNhanLoiMoi() {
        val bridge = TesterInviteBridge()
        val actions = LocalDeviceActions(
            FakePlatform(),
            FakeClipboard(),
            ForegroundObservationStore(),
            automationProvider = { LocalSafeUiAutomation(bridge) },
        )

        val result = actions.execute(command("ACCEPT_THREADS_TESTER"))

        // Bấm được nút Chấp nhận là coi như xong; không chờ đoán màn hình sau đó.
        assertEquals("completed", result.result["flow_status"])
        assertEquals("THREADS_TESTER_INVITE_LIST", result.result["screen"])
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun confirmThreadsOauthBamNutCapQuyen() {
        val bridge = ConsentBridge()
        val actions = LocalDeviceActions(
            FakePlatform(),
            FakeClipboard(),
            ForegroundObservationStore(),
            automationProvider = { LocalSafeUiAutomation(bridge) },
        )

        val result = actions.execute(command("CONFIRM_THREADS_OAUTH"))

        assertEquals("completed", result.result["flow_status"])
        assertEquals("THREADS_OAUTH_CONSENT", result.result["screen"])
        assertEquals(1, bridge.clicks.size)
    }

    @Test
    fun haiLenhMoiKhongConBiTraVeUnsupported() {
        val actions = LocalDeviceActions(
            FakePlatform(),
            FakeClipboard(),
            ForegroundObservationStore(),
            automationProvider = { LocalSafeUiAutomation(TesterInviteBridge()) },
        )

        listOf("ACCEPT_THREADS_TESTER", "CONFIRM_THREADS_OAUTH").forEach { action ->
            val result = actions.execute(command(action))
            assertFalse(result.result["error_code"] == "UNSUPPORTED_ACTION")
        }
    }
}
