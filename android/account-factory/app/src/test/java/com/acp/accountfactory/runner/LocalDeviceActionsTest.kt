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
}
