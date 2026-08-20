package com.acp.accountfactory.settings

import android.content.Context
import com.acp.accountfactory.network.FactoryConnection

class FactorySettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(
        "factory_settings",
        Context.MODE_PRIVATE,
    )
    private val secureTokenStore = SecureDeviceTokenStore(context)

    var baseUrl: String
        get() = prefs.getString("base_url", "") ?: ""
        set(value) = prefs.edit().putString("base_url", value.trim()).apply()

    var factoryKey: String
        get() = prefs.getString("factory_key", "") ?: ""
        set(value) = prefs.edit().putString("factory_key", value.trim()).apply()

    val deviceToken: String
        get() = secureTokenStore.get()

    fun saveEnrollment(controllerUrl: String, deviceToken: String) {
        baseUrl = controllerUrl
        secureTokenStore.set(deviceToken)
        factoryKey = ""
    }

    fun clearEnrollment() {
        secureTokenStore.clear()
    }

    fun connection(): FactoryConnection {
        val credential = deviceToken.ifBlank { factoryKey }
        return FactoryConnection(baseUrl, credential)
    }

    fun isConfigured(): Boolean =
        baseUrl.isNotBlank() && (deviceToken.isNotBlank() || factoryKey.isNotBlank())
}
