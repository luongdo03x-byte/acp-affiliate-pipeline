package com.acp.accountfactory.ui

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FactoryPresentationTest {
    @Test
    fun capacityLabelsAreOperatorFriendly() {
        assertEquals("Ổn định", capacityLabel("GREEN"))
        assertEquals("Theo dõi", capacityLabel("YELLOW"))
        assertEquals("Giảm tải", capacityLabel("RED"))
        assertEquals("Khẩn cấp", capacityLabel("EMERGENCY"))
        assertEquals("Không rõ", capacityLabel("UNKNOWN"))
    }

    @Test
    fun checkpointActionsOnlyEnableForActionableStates() {
        assertTrue(checkpointActionsEnabled("OPEN"))
        assertTrue(checkpointActionsEnabled("SNOOZED"))
        assertFalse(checkpointActionsEnabled("VERIFYING"))
        assertFalse(checkpointActionsEnabled("RESOLVED"))
    }

    @Test
    fun formatsWaitingDurationWithoutUsingDeviceLocalWorkflowState() {
        val now = Instant.parse("2026-08-17T06:45:00Z")
        assertEquals("45 phút", waitingDuration("2026-08-17T06:00:00Z", now))
        assertEquals("2 giờ 5 phút", waitingDuration("2026-08-17T04:40:00Z", now))
    }
}
