package com.acp.accountfactory.ui

import com.acp.accountfactory.network.FactoryRunnerDto
import org.junit.Assert.assertEquals
import org.junit.Test

class CreateAccountPresentationTest {
    private fun runner(
        id: String,
        type: String,
        state: String = "READY",
        draining: Boolean = false,
        deviceId: String? = null,
        avdName: String? = null,
    ) = FactoryRunnerDto(
        id = id,
        runnerType = type,
        deviceId = deviceId,
        deviceName = if (type == "LOCAL_DEVICE") "Pixel" else null,
        avdName = avdName,
        state = state,
        currentAccountId = null,
        currentJobId = null,
        lastHeartbeatAt = null,
        draining = draining,
        lastError = null,
    )

    @Test
    fun targetOptionsIncludeThisPhoneAndReadyAvdsOnly() {
        val local = runner("phone-1", "LOCAL_DEVICE", deviceId = "local-device")
        val ready = runner("avd-1", "REMOTE_AVD", avdName = "acp-worker-01")
        val draining = runner("avd-2", "REMOTE_AVD", draining = true, avdName = "acp-worker-02")

        val options = buildExecutionTargets("local-device", listOf(local, ready, draining))

        assertEquals(listOf("THIS_PHONE", "AUTO_AVD", "avd-1"), options.map { it.value })
    }

    @Test
    fun thisPhoneMapsToRegisteredLocalWorkerId() {
        val local = runner("phone-1", "LOCAL_DEVICE", deviceId = "local-device")
        val target = buildExecutionTargets("local-device", listOf(local)).first()
        assertEquals("phone-1", target.controllerValue)
    }
}
