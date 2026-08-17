package com.acp.accountfactory.settings

import android.content.Context
import com.acp.accountfactory.network.FactoryConnection

class FactorySettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(
        "factory_settings",
        Context.MODE_PRIVATE,
    )

    var baseUrl: String
        get() = prefs.getString("base_url", "") ?: ""
        set(value) = prefs.edit().putString("base_url", value.trim()).apply()

    var factoryKey: String
        get() = prefs.getString("factory_key", "") ?: ""
        set(value) = prefs.edit().putString("factory_key", value.trim()).apply()

    fun connection(): FactoryConnection = FactoryConnection(baseUrl, factoryKey)
    fun isConfigured(): Boolean = baseUrl.isNotBlank() && factoryKey.isNotBlank()
}
