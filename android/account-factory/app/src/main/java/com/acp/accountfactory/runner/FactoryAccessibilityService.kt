package com.acp.accountfactory.runner

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

class FactoryAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
            event.eventType != AccessibilityEvent.TYPE_WINDOWS_CHANGED
        ) return

        observationStore.update(
            packageName = event.packageName?.toString(),
            className = event.className?.toString(),
            observedAtEpochMs = System.currentTimeMillis(),
        )
    }

    override fun onInterrupt() = Unit

    companion object {
        val observationStore = ForegroundObservationStore()
    }
}
