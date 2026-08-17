package com.acp.accountfactory.ui

import com.acp.accountfactory.network.AccountCountsDto
import com.acp.accountfactory.network.BatchSummaryDto
import com.acp.accountfactory.network.CommandAcceptedDto
import com.acp.accountfactory.network.DashboardDto
import com.acp.accountfactory.network.FactoryAccountDto
import com.acp.accountfactory.network.FactoryCheckpointDto
import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryV2ApiClient
import com.acp.accountfactory.network.FactoryWorkerDto
import com.acp.accountfactory.network.StartedOAuthDto
import com.acp.accountfactory.network.WorkerCountsDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class FactoryViewModelTest {
    private val dispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun refreshReplacesAccountStageWithControllerSnapshot() = runTest {
        val api = FakeFactoryApi()
        val vm = FactoryViewModel(api) { FactoryConnection("https://acp.example", "test-key") }

        vm.refresh().join()
        assertEquals("PROFILE_READY", vm.state.value.accounts.single().stage)

        api.accountStage = "WAITING_HUMAN"
        vm.refresh().join()

        assertEquals("WAITING_HUMAN", vm.state.value.accounts.single().stage)
        assertEquals(2, api.refreshCount)
    }

    @Test
    fun continueDoesNotLocallyMarkInstagramCreated() = runTest {
        val api = FakeFactoryApi().apply { accountStage = "WAITING_HUMAN" }
        val vm = FactoryViewModel(api) { FactoryConnection("https://acp.example", "test-key") }
        vm.refresh().join()

        vm.continueCheckpoint("cp-1").join()

        assertEquals(listOf("continue:cp-1"), api.actions)
        assertEquals("WAITING_HUMAN", vm.state.value.accounts.single().stage)
        assertTrue(api.refreshCount >= 2)
    }

    @Test
    fun oauthEmitsOfficialUrlAndRefreshesActiveStateFromServer() = runTest {
        val api = FakeFactoryApi().apply { accountStage = "THREADS_CREATED" }
        val vm = FactoryViewModel(
            api = api,
            connectionProvider = { FactoryConnection("https://acp.example", "test-key") },
            oauthPollDelayMs = 0,
            oauthMaxAttempts = 2,
        )
        vm.refresh().join()
        val event = async { vm.events.first() }

        vm.startOAuth("acc-1").join()

        assertEquals(
            FactoryUiEvent.OpenExternalUrl("https://threads.example/authorize?state=test"),
            event.await(),
        )
        assertEquals("ACP_ACTIVE", vm.state.value.accounts.single().stage)
        assertEquals("threads_maianh_le", vm.state.value.accounts.single().channelCode)
    }

    private class FakeFactoryApi : FactoryV2ApiClient {
        var accountStage = "PROFILE_READY"
        var channelCode: String? = null
        var refreshCount = 0
        val actions = mutableListOf<String>()

        override suspend fun dashboard(connection: FactoryConnection): DashboardDto {
            refreshCount += 1
            return DashboardDto(
                batch = BatchSummaryDto("batch-1", "Batch 01", "RUNNING", 50),
                accounts = AccountCountsDto(1, if (accountStage == "ACP_ACTIVE") 1 else 0, 0, if (accountStage == "WAITING_HUMAN") 1 else 0, 0, if (accountStage == "PROFILE_READY") 1 else 0),
                workers = WorkerCountsDto(1, 0, if (accountStage == "WAITING_HUMAN") 1 else 0, 0),
                host = null,
            )
        }

        override suspend fun accounts(connection: FactoryConnection): List<FactoryAccountDto> = listOf(
            account()
        )

        override suspend fun workers(connection: FactoryConnection): List<FactoryWorkerDto> = emptyList()

        override suspend fun checkpoints(connection: FactoryConnection): List<FactoryCheckpointDto> = listOf(
            FactoryCheckpointDto(
                id = "cp-1", batchId = "batch-1", accountId = "acc-1", workerId = "worker-1",
                type = "IG_POSTCHECK", status = "OPEN", message = "Confirm", createdAt = "2026-08-17T06:00:00+00:00",
                nextReminderAt = null, snoozedUntil = null,
            )
        )

        override suspend fun continueCheckpoint(connection: FactoryConnection, id: String): CommandAcceptedDto {
            actions += "continue:$id"
            return CommandAcceptedDto("cmd-1", "VERIFYING")
        }

        override suspend fun retryCheckpoint(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-2", "VERIFYING")
        override suspend fun snoozeCheckpoint(connection: FactoryConnection, id: String, minutes: Int) = CommandAcceptedDto("cmd-3", "SNOOZED")
        override suspend fun pauseBatch(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-4", "PAUSED")
        override suspend fun resumeBatch(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-5", "RUNNING")
        override suspend fun stopAccount(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-6", "DISABLED")
        override suspend fun retryAccount(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-7", "RETRY_PENDING")
        override suspend fun drainWorker(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-8", "DRAINING")
        override suspend fun restartWorker(connection: FactoryConnection, id: String) = CommandAcceptedDto("cmd-9", "RECOVERING")

        override suspend fun startOAuth(connection: FactoryConnection, id: String): StartedOAuthDto {
            accountStage = "ACP_CONNECTING"
            return StartedOAuthDto(
                sessionId = "session-1",
                authorizationUrl = "https://threads.example/authorize?state=test",
                status = "WAITING_AUTH",
                expiresAt = "2026-08-17T07:00:00Z",
            )
        }

        override suspend fun oauthStatus(connection: FactoryConnection, id: String): FactoryAccountDto {
            accountStage = "ACP_ACTIVE"
            channelCode = "threads_maianh_le"
            return account()
        }

        private fun account() = FactoryAccountDto(
            id = "acc-1", batchId = "batch-1", sequence = 1, groupNo = 1,
            username = "mai.anh", displayName = "Mai Anh", bio = null,
            stage = accountStage, lastSafeStage = if (accountStage == "ACP_ACTIVE") "ACP_ACTIVE" else "PROFILE_READY",
            assignedWorkerId = "worker-1", currentJobId = "job-1", channelCode = channelCode,
            lastErrorCode = null, lastErrorMessage = null,
        )
    }
}
