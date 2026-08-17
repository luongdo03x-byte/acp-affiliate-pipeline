package com.acp.accountfactory.runner

data class LocalRunnerIdentity(
    val deviceId: String,
    val deviceName: String,
)

data class ForegroundObservation(
    val packageName: String?,
    val className: String?,
    val observedAtEpochMs: Long,
)

data class RunnerCommandResult(
    val status: String,
    val result: Map<String, Any?> = emptyMap(),
)
