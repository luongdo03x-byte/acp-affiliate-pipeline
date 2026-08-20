package com.acp.accountfactory.runner

import android.content.Context
import android.os.Build
import java.util.UUID

interface LocalIdentityStorage {
    fun get(key: String): String?
    fun put(key: String, value: String)
}

private class SharedPreferencesIdentityStorage(context: Context) : LocalIdentityStorage {
    private val prefs = context.getSharedPreferences("factory_local_runner", Context.MODE_PRIVATE)
    override fun get(key: String): String? = prefs.getString(key, null)
    override fun put(key: String, value: String) {
        prefs.edit().putString(key, value).apply()
    }
}

class LocalRunnerIdentityStore(
    private val storage: LocalIdentityStorage,
    private val deviceNameProvider: () -> String,
    private val idProvider: () -> String = { "local-" + UUID.randomUUID().toString() },
) {
    constructor(context: Context) : this(
        storage = SharedPreferencesIdentityStorage(context.applicationContext),
        deviceNameProvider = {
            listOf(Build.MANUFACTURER, Build.MODEL)
                .map { it.trim() }
                .filter { it.isNotBlank() }
                .joinToString(" ")
                .ifBlank { "Android device" }
                .take(160)
        },
    )

    fun getOrCreate(): LocalRunnerIdentity {
        val existing = storage.get(KEY_DEVICE_ID)?.takeIf { it.isNotBlank() }
        val deviceId = existing ?: idProvider().also { storage.put(KEY_DEVICE_ID, it) }
        return LocalRunnerIdentity(
            deviceId = deviceId,
            deviceName = deviceNameProvider().ifBlank { "Android device" }.take(160),
        )
    }

    private companion object {
        const val KEY_DEVICE_ID = "device_id"
    }
}
