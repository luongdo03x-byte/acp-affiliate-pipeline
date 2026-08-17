package com.acp.accountfactory.runner

import com.acp.accountfactory.network.AccountCountsDto
import com.acp.accountfactory.network.CommandAcceptedDto
import com.acp.accountfactory.network.DashboardDto
import com.acp.accountfactory.network.FactoryAccountDto
import com.acp.accountfactory.network.FactoryCheckpointDto
import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryRunnerDto
import com.acp.accountfactory.network.FactoryV2ApiClient
import com.acp.accountfactory.network.FactoryWorkerDto
import com.acp.accountfactory.network.RunnerCommandDto
import com.acp.accountfactory.network.StartedOAuthDto
import com.acp.accountfactory.network.WorkerCountsDto
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class LocalDeviceRunnerTest {
    private class FakeStorage : LocalIdentityStorage {
        private val values = mutableMapOf<String, String>()
        override fun get(key: String): String? = values[key]
        override fun put(key: String, value: String) { values[key] = value }
    }

    private class FakeActions : LocalDeviceActions(
        platform = object : LocalPlatform {
            override fun openPackage(packageName: String) = true
            override fun openUrl(url: String) = true
        },
        clipboard = object : LocalClipboard { override fun putText(text: String) = Unit },
        observationStore = ForegroundObservationStore(),
    ) {
        var calls = 0
        override fun execute(command: RunnerCommandDto): RunnerCommandResult {
            calls += 1
            return RunnerCommandResult("COMPLETED", mapOf("prepared" to true))
        }
    }

    private class FakeApi : FactoryV2ApiClient {
        var registerCalls = 0
        var lastHeartbeatWorkerId: String? = null
        val commands = ArrayDeque<RunnerCommandDto>()
        val submitted = mutableListOf<String>()
        var failFirstSubmit = false

        override suspend fun registerLocalRunner(connection: FactoryConnection, deviceId: String, deviceName: String): FactoryRunnerDto {
            registerCalls += 1
            return runner()
        }

        override suspend fun heartbeatRunner(connection: FactoryConnection, workerId: String, currentAccountId: String?, currentJobId: String?): FactoryRunnerDto {
            lastHeartbeatWorkerId = workerId
            return runner(currentAccountId, currentJobId)
        }

        override suspend fun nextRunnerCommand(connection: FactoryConnection, workerId: String): RunnerCommandDto? =
            commands.removeFirstOrNull()

        override suspend fun submitRunnerCommandResult(connection: FactoryConnection, workerId: String, commandId: String, status: String, result: Map<String, Any?>): CommandAcceptedDto {
            if (failFirstSubmit) {
                failFirstSubmit = false
                throw IllegalStateException("network")
            }
            submitted += commandId
            return CommandAcceptedDto(commandId, status)
        }

        override suspend fun runners(connection: FactoryConnection): List<FactoryRunnerDto> = listOf(runner())

        private fun runner(account: String? = null, job: String? = null) = FactoryRunnerDto(
            id = "phone-1", runnerType = "LOCAL_DEVICE", deviceId = "local-1",
            deviceName = "Pixel", avdName = null, state = if (job == null) "READY" else "RUNNING",
            currentAccountId = account, currentJobId = job, lastHeartbeatAt = null,
            draining = false, lastError = null,
        )

        override suspend fun dashboard(connection: FactoryConnection) = DashboardDto(null, AccountCountsDto(0,0,0,0,0,0), WorkerCountsDto(0,0,0,0), null)
        override suspend fun accounts(connection: FactoryConnection): List<FactoryAccountDto> = emptyList()
        override suspend fun workers(connection: FactoryConnection): List<FactoryWorkerDto> = emptyList()
        override suspend fun checkpoints(connection: FactoryConnection): List<FactoryCheckpointDto> = emptyList()
        override suspend fun continueCheckpoint(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun retryCheckpoint(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun snoozeCheckpoint(connection: FactoryConnection, id: String, minutes: Int) = error("unused")
        override suspend fun pauseBatch(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun resumeBatch(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun stopAccount(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun retryAccount(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun drainWorker(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun restartWorker(connection: FactoryConnection, id: String) = error("unused")
        override suspend fun startOAuth(connection: FactoryConnection, id: String): StartedOAuthDto = error("unused")
        override suspend fun oauthStatus(connection: FactoryConnection, id: String): FactoryAccountDto = error("unused")
    }

    private fun identityStore() = LocalRunnerIdentityStore(
        storage = FakeStorage(), deviceNameProvider = { "Pixel" }, idProvider = { "local-1" }
    )

    @Test
    fun runnerRegistersThenHeartbeatsWithReturnedWorkerId() = runTest {
        val api = FakeApi()
        val runner = LocalDeviceRunner(
            api = api,
            connectionProvider = { FactoryConnection("https://acp.example", "key") },
            identityStore = identityStore(),
            actions = FakeActions(),
            dispatcher = StandardTestDispatcher(testScheduler),
            clockMs = { 20_000L },
        )

        runner.runSingleIterationForTest()

        assertEquals(1, api.registerCalls)
        assertEquals("phone-1", api.lastHeartbeatWorkerId)
    }

    @Test
    fun runnerExecutesDeliveredCommandAndSubmitsOneResult() = runTest {
        val api = FakeApi()
        api.commands += RunnerCommandDto("c1", "j1", "a1", "PREPARE_TEXT", mapOf("text" to "@mai"), null)
        val actions = FakeActions()
        val runner = LocalDeviceRunner(api, { FactoryConnection("https://acp.example", "key") }, identityStore(), actions)

        runner.runSingleIterationForTest()

        assertEquals(1, actions.calls)
        assertEquals(listOf("c1"), api.submitted)
    }

    @Test
    fun failedSubmissionRetriesSameResultWithoutExecutingCommandTwice() = runTest {
        val api = FakeApi().apply { failFirstSubmit = true }
        api.commands += RunnerCommandDto("c1", "j1", "a1", "PREPARE_TEXT", mapOf("text" to "@mai"), null)
        val actions = FakeActions()
        val runner = LocalDeviceRunner(api, { FactoryConnection("https://acp.example", "key") }, identityStore(), actions)

        runCatching { runner.runSingleIterationForTest() }
        runner.runSingleIterationForTest()

        assertEquals(1, actions.calls)
        assertEquals(listOf("c1"), api.submitted)
    }
}
