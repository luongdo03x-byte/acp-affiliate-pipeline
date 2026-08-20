package com.acp.accountfactory.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class FactoryV2ApiMappingTest {
    @Test
    fun mapsDashboardControllerJsonExactly() {
        val json = """
            {
              "ok": true,
              "batch": {"id":"batch-1","status":"RUNNING","target_count":50,"name":"Batch 01"},
              "accounts": {"total":50,"active":18,"running":6,"waiting_human":2,"error":1,"queued":23},
              "workers": {"total":7,"running":5,"waiting_human":2,"starting":0},
              "host": {"cpu_percent":58.0,"ram_available_mb":8400,"swap_used_mb":200,"capacity_state":"YELLOW"}
            }
        """.trimIndent()

        val dto = FactoryV2Json.parseDashboard(json)

        assertEquals("batch-1", dto.batch?.id)
        assertEquals("RUNNING", dto.batch?.status)
        assertEquals(50, dto.accounts.total)
        assertEquals(2, dto.accounts.waitingHuman)
        assertEquals(7, dto.workers.total)
        assertEquals(2, dto.workers.waitingHuman)
        assertEquals("YELLOW", dto.host?.capacityState)
        assertEquals(8400, dto.host?.ramAvailableMb)
    }

    @Test
    fun mapsAccountAndCheckpointControllerJsonExactly() {
        val accountJson = """
            {"ok":true,"accounts":[{"id":"acc-17","batch_id":"batch-1","sequence":17,"group_no":4,"username":"mai.anh","display_name":"Mai Anh","bio":"daily finds","stage":"WAITING_HUMAN","last_safe_stage":"PROFILE_READY"}]}
        """.trimIndent()
        val checkpointJson = """
            {"ok":true,"checkpoints":[{"id":"cp-1","batch_id":"batch-1","account_id":"acc-17","worker_id":"worker-03","type":"IG_POSTCHECK","status":"OPEN","message":"Confirm state","created_at":"2026-08-17T06:00:00+00:00"}]}
        """.trimIndent()

        val account = FactoryV2Json.parseAccounts(accountJson).single()
        val checkpoint = FactoryV2Json.parseCheckpoints(checkpointJson).single()

        assertEquals("acc-17", account.id)
        assertEquals("mai.anh", account.username)
        assertEquals("WAITING_HUMAN", account.stage)
        assertEquals("cp-1", checkpoint.id)
        assertEquals("worker-03", checkpoint.workerId)
        assertNotNull(checkpoint.message)
    }
}
