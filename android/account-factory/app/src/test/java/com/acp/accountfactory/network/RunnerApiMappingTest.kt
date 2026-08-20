package com.acp.accountfactory.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RunnerApiMappingTest {
    @Test
    fun mapsLocalRunnerWithoutAdbFields() {
        val json = """
            {"ok":true,"runners":[{"id":"phone-1","runner_type":"LOCAL_DEVICE","device_id":"abc","device_name":"Pixel","avd_name":null,"state":"READY","draining":0}]}
        """.trimIndent()

        val dto = FactoryV2Json.parseRunners(json).single()

        assertEquals("LOCAL_DEVICE", dto.runnerType)
        assertEquals("abc", dto.deviceId)
        assertEquals("Pixel", dto.deviceName)
        assertNull(dto.avdName)
    }

    @Test
    fun mapsRunnerCommandAllowedPayload() {
        val json = """
            {"ok":true,"command":{"id":"c1","job_id":"j1","account_id":"a1","action":"OPEN_PACKAGE","payload":{"package":"com.instagram.android"},"created_at":"2026-08-17T07:00:00+00:00"}}
        """.trimIndent()

        val dto = FactoryV2Json.parseRunnerCommandResponse(json)!!

        assertEquals("OPEN_PACKAGE", dto.action)
        assertEquals("com.instagram.android", dto.payload["package"])
    }

    @Test
    fun mapsNullRunnerCommand() {
        val dto = FactoryV2Json.parseRunnerCommandResponse("""{"ok":true,"command":null}""")
        assertNull(dto)
    }

    @Test
    fun mapsCreatedAccountExecutionTarget() {
        val json = """
            {"ok":true,"account":{"id":"a1","batch_id":"b1","sequence":1,"group_no":1,"username":"mai","display_name":"Mai","stage":"PROFILE_READY","last_safe_stage":"PROFILE_READY","execution_target":"phone-1","assigned_worker_id":null}}
        """.trimIndent()

        val dto = FactoryV2Json.parseAccountResponse(json)

        assertEquals("phone-1", dto.executionTarget)
        assertNull(dto.assignedWorkerId)
    }
}
