package com.acp.accountfactory.runner

import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryRunnerDto
import com.acp.accountfactory.network.FactoryV2ApiClient
import com.acp.accountfactory.network.RunnerCommandDto
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class LocalDeviceRunner(
    private val api: FactoryV2ApiClient,
    private val connectionProvider: () -> FactoryConnection,
    private val identityStore: LocalRunnerIdentityStore,
    private val actions: LocalDeviceActions,
    private val dispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val clockMs: () -> Long = System::currentTimeMillis,
    private val heartbeatIntervalMs: Long = 10_000L,
    private val activePollMs: Long = 1_000L,
    private val idlePollMs: Long = 5_000L,
) {
    private data class PendingSubmission(
        val command: RunnerCommandDto,
        val result: RunnerCommandResult,
    )

    private var scope: CoroutineScope? = null
    private var loopJob: Job? = null
    private var registered: FactoryRunnerDto? = null
    private var currentAccountId: String? = null
    private var currentJobId: String? = null
    private var lastHeartbeatMs: Long = Long.MIN_VALUE
    private var pendingSubmission: PendingSubmission? = null

    @Synchronized
    fun start() {
        if (loopJob?.isActive == true) return
        val createdScope = CoroutineScope(SupervisorJob() + dispatcher)
        scope = createdScope
        loopJob = createdScope.launch {
            while (isActive) {
                val delayMs = try {
                    runSingleIterationForTest()
                    if (currentJobId != null) activePollMs else idlePollMs
                } catch (_: Exception) {
                    idlePollMs
                }
                delay(delayMs.coerceAtLeast(250L))
            }
        }
    }

    @Synchronized
    fun stop() {
        loopJob?.cancel()
        loopJob = null
        scope?.cancel()
        scope = null
    }

    suspend fun runSingleIterationForTest() = withContext(dispatcher) {
        val connection = connectionProvider()
        val runner = ensureRegistered(connection)

        pendingSubmission?.let { pending ->
            submitPending(connection, runner.id, pending)
            pendingSubmission = null
        }

        val command = api.nextRunnerCommand(connection, runner.id)
        if (command != null) {
            currentAccountId = command.accountId
            currentJobId = command.jobId
            val result = actions.execute(command)
            val pending = PendingSubmission(command, result)
            pendingSubmission = pending
            submitPending(connection, runner.id, pending)
            pendingSubmission = null
        } else {
            syncAuthoritativeAssignment(connection, runner.id)
        }

        val currentTime = clockMs()
        if (lastHeartbeatMs == Long.MIN_VALUE || currentTime - lastHeartbeatMs >= heartbeatIntervalMs) {
            heartbeat(connection, runner.id)
            lastHeartbeatMs = currentTime
        }
    }

    private suspend fun ensureRegistered(connection: FactoryConnection): FactoryRunnerDto {
        registered?.let { return it }
        val identity = identityStore.getOrCreate()
        return api.registerLocalRunner(connection, identity.deviceId, identity.deviceName).also {
            registered = it
            currentAccountId = it.currentAccountId
            currentJobId = it.currentJobId
        }
    }

    private suspend fun syncAuthoritativeAssignment(connection: FactoryConnection, workerId: String) {
        val latest = api.runners(connection).firstOrNull { it.id == workerId } ?: return
        registered = latest
        currentAccountId = latest.currentAccountId
        currentJobId = latest.currentJobId
    }

    private suspend fun heartbeat(connection: FactoryConnection, workerId: String) {
        try {
            val response = api.heartbeatRunner(
                connection,
                workerId,
                currentAccountId,
                currentJobId,
            )
            registered = response
            currentAccountId = response.currentAccountId
            currentJobId = response.currentJobId
        } catch (first: Exception) {
            syncAuthoritativeAssignment(connection, workerId)
            val response = api.heartbeatRunner(
                connection,
                workerId,
                currentAccountId,
                currentJobId,
            )
            registered = response
            currentAccountId = response.currentAccountId
            currentJobId = response.currentJobId
        }
    }

    private suspend fun submitPending(
        connection: FactoryConnection,
        workerId: String,
        pending: PendingSubmission,
    ) {
        api.submitRunnerCommandResult(
            connection = connection,
            workerId = workerId,
            commandId = pending.command.id,
            status = pending.result.status,
            result = pending.result.result,
        )
    }
}
