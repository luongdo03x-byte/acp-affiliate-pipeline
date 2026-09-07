package com.acp.accountfactory.network

import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class CloudPairingTest {
    @Test
    fun pairingCodeIsSentInBodyAndNotUrlOrOperatorHeader() {
        val request = cloudPairingRequest("https://factory.example/", "one-use-code", "phone-12345678", "My Phone")
        assertEquals("https://factory.example/api/factory/pair", request.url.toString())
        assertEquals("POST", request.method)
        assertNull(request.header("X-ACP-Factory-Key"))
        val buffer = Buffer()
        request.body!!.writeTo(buffer)
        val json = JSONObject(buffer.readUtf8())
        assertEquals("one-use-code", json.getString("code"))
        assertEquals("phone-12345678", json.getString("device_id"))
    }

    @Test
    fun rejectsPlaintextCredentialOriginsAndUnexpectedPaths() {
        for (url in listOf("http://factory.example", "https://user:password@factory.example",
            "https://factory.example/other", "https://factory.example/?code=secret", "https://factory.example/#secret")) {
            assertThrows(IllegalArgumentException::class.java) {
                cloudPairingRequest(url, "code", "phone-12345678", "Phone")
            }
        }
    }

    @Test
    fun cloudPairingResponseUsesSameDeviceTokenContractAsEnrollment() {
        val result = ControllerDiscovery.parseEnrollment(
            """{"ok":true,"service":"account-factory","api_version":2,"device_token":"test-only-device-token"}"""
        )
        assertEquals("test-only-device-token", result?.deviceToken)
    }
}
