package com.acp.accountfactory.runner

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ForegroundObservationStoreTest {
    @Test
    fun latestObservationReplacesOlderValue() {
        val store = ForegroundObservationStore()
        store.update("com.instagram.android", "LoginActivity", 100L)
        store.update("com.instagram.barcelona", "MainActivity", 200L)

        val latest = store.latest()
        assertEquals("com.instagram.barcelona", latest.packageName)
        assertEquals("MainActivity", latest.className)
        assertEquals(200L, latest.observedAtEpochMs)
    }

    @Test
    fun emptyStoreReturnsNoPackage() {
        val store = ForegroundObservationStore()
        assertNull(store.latest().packageName)
    }
}
