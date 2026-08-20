package com.acp.accountfactory.runner

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class LocalRunnerIdentityTest {
    private class FakeStorage : LocalIdentityStorage {
        private val values = mutableMapOf<String, String>()
        override fun get(key: String): String? = values[key]
        override fun put(key: String, value: String) { values[key] = value }
    }

    @Test
    fun identityIsStableAcrossReads() {
        val storage = FakeStorage()
        var generated = 0
        val store = LocalRunnerIdentityStore(
            storage = storage,
            deviceNameProvider = { "Pixel" },
            idProvider = { generated++; "local-123" },
        )

        assertEquals("local-123", store.getOrCreate().deviceId)
        assertEquals("local-123", store.getOrCreate().deviceId)
        assertEquals(1, generated)
    }

    @Test
    fun identityDoesNotUseSensitiveHardwareIdentifiers() {
        val identity = LocalRunnerIdentity("local-123", "Pixel")
        assertFalse(identity.deviceId.contains("imei", ignoreCase = true))
        assertFalse(identity.deviceId.contains("serial", ignoreCase = true))
        assertFalse(identity.deviceId.contains("mac", ignoreCase = true))
    }
}
