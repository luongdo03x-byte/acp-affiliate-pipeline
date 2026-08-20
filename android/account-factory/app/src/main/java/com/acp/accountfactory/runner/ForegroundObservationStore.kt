package com.acp.accountfactory.runner

import java.util.concurrent.atomic.AtomicReference

class ForegroundObservationStore {
    private val current = AtomicReference(
        ForegroundObservation(
            packageName = null,
            className = null,
            observedAtEpochMs = 0L,
        )
    )

    fun update(packageName: String?, className: String?, observedAtEpochMs: Long) {
        current.set(
            ForegroundObservation(
                packageName = packageName?.take(240),
                className = className?.take(240),
                observedAtEpochMs = observedAtEpochMs,
            )
        )
    }

    fun latest(): ForegroundObservation = current.get()
}
