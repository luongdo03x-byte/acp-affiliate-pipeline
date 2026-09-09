package com.acp.accountfactory.runner

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import com.acp.accountfactory.network.RunnerCommandDto

interface LocalPlatform {
    fun openPackage(packageName: String): Boolean
    fun openUrl(url: String): Boolean
}

interface LocalClipboard {
    fun putText(text: String)
}

private class AndroidLocalPlatform(private val context: Context) : LocalPlatform {
    override fun openPackage(packageName: String): Boolean {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return true
    }

    override fun openUrl(url: String): Boolean {
        val uri = runCatching { Uri.parse(url) }.getOrNull() ?: return false
        if (!uri.scheme.equals("https", ignoreCase = true)) return false
        val intent = Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return true
    }
}

private class AndroidLocalClipboard(context: Context) : LocalClipboard {
    private val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    override fun putText(text: String) {
        clipboard.setPrimaryClip(ClipData.newPlainText("Account Factory", text))
    }
}

open class LocalDeviceActions(
    private val platform: LocalPlatform,
    private val clipboard: LocalClipboard,
    private val observationStore: ForegroundObservationStore,
    private val automationProvider: () -> LocalSafeUiAutomation? = { null },
    private val uiTransitionWait: () -> Unit = { Thread.sleep(700L) },
) {
    private val uiSettlingAttempts = mutableMapOf<String, Int>()

    constructor(context: Context) : this(
        platform = AndroidLocalPlatform(context.applicationContext),
        clipboard = AndroidLocalClipboard(context.applicationContext),
        observationStore = FactoryAccessibilityService.observationStore,
        automationProvider = {
            FactoryAccessibilityService.instance?.let(::LocalSafeUiAutomation)
        },
    )

    open fun execute(command: RunnerCommandDto): RunnerCommandResult {
        val action = command.action.uppercase()
        if (command.payload.keys.any { it.lowercase() in SENSITIVE_KEYS }) {
            return failed("SENSITIVE_PAYLOAD")
        }
        return try {
            when (action) {
                "PREPARE_INSTAGRAM" -> prepareInstagram()
                "AUTOMATE_INSTAGRAM" -> automate(command, "instagram")
                "AUTOMATE_THREADS" -> automate(command, "threads")
                "OBSERVE_CHECKPOINT" -> observeCheckpoint(command)
                "OPEN_PACKAGE" -> openPackage(command)
                "OPEN_URL" -> openUrl(command)
                "PREPARE_TEXT" -> prepareText(command)
                "OBSERVE_FOREGROUND" -> observeForeground()
                "REPORT_WAITING_HUMAN" -> RunnerCommandResult(
                    status = "COMPLETED",
                    result = mapOf("waiting_human" to true),
                )
                else -> failed("UNSUPPORTED_ACTION")
            }
        } catch (exception: Exception) {
            runCatching {
                Log.w(TAG, "local action failed: action=$action type=${exception.javaClass.simpleName}")
            }
            failed("LOCAL_ACTION_FAILED")
        }
    }

    private fun prepareInstagram(): RunnerCommandResult =
        if (platform.openPackage(LocalSafeUiAutomation.INSTAGRAM_PACKAGE)) completed()
        else failed("PACKAGE_NOT_INSTALLED")

    private fun automate(command: RunnerCommandDto, flow: String): RunnerCommandResult {
        val packageName = if (flow == "threads") {
            LocalSafeUiAutomation.THREADS_PACKAGE
        } else {
            LocalSafeUiAutomation.INSTAGRAM_PACKAGE
        }
        val automation = automationProvider() ?: return flowResult(
            LocalFlowOutcome("needs_confirmation", "UNKNOWN", "ACCESSIBILITY_NOT_READY")
        )
        if (automationForegroundPackage(automation) != packageName) {
            if (!platform.openPackage(packageName)) return failed("PACKAGE_NOT_INSTALLED")
            return flowResult(LocalFlowOutcome("running", "APP_OPENING"))
        }
        val profile = command.payload.filterKeys { it in PROFILE_FIELDS }
        var outcome = runAutomationStep(automation, flow, profile)
        repeat(ACCOUNT_SWITCH_CHAIN_STEPS) {
            if (outcome.status != "running" || outcome.screen !in ACCOUNT_SWITCH_SCREENS) {
                return@repeat
            }
            uiTransitionWait()
            outcome = runAutomationStep(automation, flow, profile)
        }
        if (outcome.status == "needs_confirmation" && outcome.reason == "UI_CHANGED") {
            val attempts = (uiSettlingAttempts[flow] ?: 0) + 1
            uiSettlingAttempts[flow] = attempts
            if (attempts <= UI_SETTLING_RETRIES) {
                return flowResult(LocalFlowOutcome("running", outcome.screen, "UI_SETTLING"))
            }
        } else {
            uiSettlingAttempts.remove(flow)
        }
        return flowResult(outcome)
    }

    private fun runAutomationStep(
        automation: LocalSafeUiAutomation,
        flow: String,
        profile: Map<String, String?>,
    ): LocalFlowOutcome =
        if (flow == "threads") automation.runThreads(profile) else automation.runInstagram(profile)

    private fun observeCheckpoint(command: RunnerCommandDto): RunnerCommandResult {
        val automation = automationProvider() ?: return flowResult(
            LocalFlowOutcome("needs_confirmation", "UNKNOWN", "ACCESSIBILITY_NOT_READY")
        )
        return flowResult(automation.observe(command.payload["flow"].orEmpty()))
    }

    private fun automationForegroundPackage(automation: LocalSafeUiAutomation): String? =
        automation.foregroundPackageForRunner()

    private fun flowResult(outcome: LocalFlowOutcome) = RunnerCommandResult(
        status = "COMPLETED",
        result = outcome.result(),
    )

    private fun openPackage(command: RunnerCommandDto): RunnerCommandResult {
        val packageName = command.payload["package"].orEmpty()
        if (packageName !in OFFICIAL_PACKAGES) return failed("PACKAGE_NOT_ALLOWED")
        return if (platform.openPackage(packageName)) completed() else failed("PACKAGE_NOT_INSTALLED")
    }

    private fun openUrl(command: RunnerCommandDto): RunnerCommandResult {
        val url = command.payload["url"].orEmpty().trim()
        if (!url.startsWith("https://", ignoreCase = true)) return failed("URL_NOT_ALLOWED")
        return if (platform.openUrl(url)) completed() else failed("URL_OPEN_FAILED")
    }

    private fun prepareText(command: RunnerCommandDto): RunnerCommandResult {
        val text = command.payload["text"].orEmpty()
        if (text.length > 500) return failed("TEXT_TOO_LONG")
        clipboard.putText(text)
        return RunnerCommandResult(
            status = "COMPLETED",
            result = mapOf("prepared" to true),
        )
    }

    private fun observeForeground(): RunnerCommandResult {
        val observation = observationStore.latest()
        return RunnerCommandResult(
            status = "COMPLETED",
            result = buildMap {
                put("package", observation.packageName)
                put("activity", observation.className)
            },
        )
    }

    private fun completed() = RunnerCommandResult(status = "COMPLETED")
    private fun failed(code: String) = RunnerCommandResult(
        status = "FAILED",
        result = mapOf("error_code" to code),
    )

    private companion object {
        const val TAG = "AcpLocalActions"
        // Accessibility gestures are asynchronous. A second action in the same
        // command can land before a long-press finishes and toggle the account
        // switcher closed again. Let the next polled command observe the stable
        // postcondition before it performs another mutation.
        const val ACCOUNT_SWITCH_CHAIN_STEPS = 0
        const val UI_SETTLING_RETRIES = 5
        val ACCOUNT_SWITCH_SCREENS = setOf(
            "IG_HOME", "IG_ACCOUNT_SWITCHER",
            "THREADS_HOME", "THREADS_ACCOUNT_SWITCHER",
        )
        val OFFICIAL_PACKAGES = setOf("com.instagram.android", "com.instagram.barcelona")
        val SENSITIVE_KEYS = setOf("password", "otp", "captcha", "token", "secret")
        val PROFILE_FIELDS = setOf(
            "username", "display_name", "bio", "signup_contact_type",
            "signup_contact", "birth_date", "avatar_file",
        )
    }
}
