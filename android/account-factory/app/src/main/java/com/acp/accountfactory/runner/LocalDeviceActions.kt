package com.acp.accountfactory.runner

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
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
) {
    constructor(context: Context) : this(
        platform = AndroidLocalPlatform(context.applicationContext),
        clipboard = AndroidLocalClipboard(context.applicationContext),
        observationStore = FactoryAccessibilityService.observationStore,
    )

    open fun execute(command: RunnerCommandDto): RunnerCommandResult {
        val action = command.action.uppercase()
        if (command.payload.keys.any { it.lowercase() in SENSITIVE_KEYS }) {
            return failed("SENSITIVE_PAYLOAD")
        }
        return try {
            when (action) {
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
        } catch (_: Exception) {
            failed("LOCAL_ACTION_FAILED")
        }
    }

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
        val OFFICIAL_PACKAGES = setOf("com.instagram.android", "com.instagram.barcelona")
        val SENSITIVE_KEYS = setOf("password", "otp", "captcha", "token", "secret")
    }
}
