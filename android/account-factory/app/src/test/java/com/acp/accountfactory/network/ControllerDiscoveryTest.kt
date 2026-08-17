package com.acp.accountfactory.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ControllerDiscoveryTest {
    @Test
    fun private24CandidatesStayOnPrivateSubnetAndExcludeCurrentDevice() {
        val candidates = ControllerDiscovery.private24Candidates("192.168.68.34", 5001)
        assertEquals(253, candidates.size)
        assertTrue(candidates.contains("http://192.168.68.1:5001"))
        assertTrue(candidates.contains("http://192.168.68.254:5001"))
        assertFalse(candidates.contains("http://192.168.68.34:5001"))
    }

    @Test
    fun private24CandidatesRejectPublicOrInvalidIpv4() {
        assertTrue(ControllerDiscovery.private24Candidates("8.8.8.8", 5001).isEmpty())
        assertTrue(ControllerDiscovery.private24Candidates("not-an-ip", 5001).isEmpty())
        assertTrue(ControllerDiscovery.private24Candidates("127.0.0.1", 5001).isEmpty())
    }

    @Test
    fun parseDiscoveryAcceptsOnlyAccountFactoryV2() {
        val valid = ControllerDiscovery.parseDiscovery(
            """{"ok":true,"service":"account-factory","api_version":2}"""
        )
        assertEquals("account-factory", valid?.service)
        assertEquals(2, valid?.apiVersion)
        assertEquals(null, ControllerDiscovery.parseDiscovery(
            """{"ok":true,"service":"something-else","api_version":2}"""
        ))
        assertEquals(null, ControllerDiscovery.parseDiscovery(
            """{"ok":true,"service":"account-factory","api_version":1}"""
        ))
    }

    @Test
    fun connectionPrefersDeviceTokenAndFallsBackToLegacyFactoryKey() {
        val device = FactoryConnection(
            baseUrl = "http://192.168.68.2:5001",
            factoryKey = "legacy",
            deviceToken = "device-token",
        )
        assertEquals("X-ACP-Device-Token", device.authHeader().first)
        assertEquals("device-token", device.authHeader().second)

        val legacy = FactoryConnection(
            baseUrl = "http://192.168.68.2:5001",
            factoryKey = "legacy",
        )
        assertEquals("X-ACP-Factory-Key", legacy.authHeader().first)
        assertEquals("legacy", legacy.authHeader().second)
    }
}
