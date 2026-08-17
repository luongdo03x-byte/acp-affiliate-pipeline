package com.acp.accountfactory.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.acp.accountfactory.network.DashboardDto
import com.acp.accountfactory.network.FactoryAccountDto
import com.acp.accountfactory.network.FactoryCheckpointDto
import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryRunnerDto
import com.acp.accountfactory.network.FactoryV2ApiClient
import com.acp.accountfactory.network.FactoryWorkerDto
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class FactoryUiState(
    val dashboard: DashboardDto? = null,
    val accounts: List<FactoryAccountDto> = emptyList(),
    val checkpoints: List<FactoryCheckpointDto> = emptyList(),
    val workers: List<FactoryWorkerDto> = emptyList(),
    val runners: List<FactoryRunnerDto> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
)

sealed interface FactoryUiEvent {
    data class OpenExternalUrl(val url: String) : FactoryUiEvent
}

class FactoryViewModel(
    private val api: FactoryV2ApiClient,
    private val connectionProvider: () -> FactoryConnection,
    private val oauthPollDelayMs: Long = 3_000L,
    private val oauthMaxAttempts: Int = 120,
) : ViewModel() {
    init {
        require(oauthPollDelayMs >= 0) { "oauthPollDelayMs must be non-negative" }
        require(oauthMaxAttempts > 0) { "oauthMaxAttempts must be positive" }
    }

    private val mutableState = MutableStateFlow(FactoryUiState())
    val state: StateFlow<FactoryUiState> = mutableState.asStateFlow()

    private val mutableEvents = MutableSharedFlow<FactoryUiEvent>(extraBufferCapacity = 1)
    val events: SharedFlow<FactoryUiEvent> = mutableEvents.asSharedFlow()

    fun refresh(): Job = viewModelScope.launch { loadSnapshot() }

    fun createAccount(executionTarget: String): Job = viewModelScope.launch {
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            val connection = connectionProvider()
            api.createAccount(connection, executionTarget)
            loadSnapshot(connection)
        } catch (error: Exception) {
            mutableState.value = mutableState.value.copy(
                loading = false,
                error = error.message?.take(300) ?: "Không thể tạo account",
            )
        }
    }

    fun continueCheckpoint(id: String): Job = command { connection ->
        api.continueCheckpoint(connection, id)
    }

    fun retryCheckpoint(id: String): Job = command { connection ->
        api.retryCheckpoint(connection, id)
    }

    fun snoozeCheckpoint(id: String, minutes: Int): Job = command { connection ->
        api.snoozeCheckpoint(connection, id, minutes)
    }

    fun pauseBatch(id: String): Job = command { connection ->
        api.pauseBatch(connection, id)
    }

    fun resumeBatch(id: String): Job = command { connection ->
        api.resumeBatch(connection, id)
    }

    fun stopAccount(id: String): Job = command { connection ->
        api.stopAccount(connection, id)
    }

    fun retryAccount(id: String): Job = command { connection ->
        api.retryAccount(connection, id)
    }

    fun drainWorker(id: String): Job = command { connection ->
        api.drainWorker(connection, id)
    }

    fun restartWorker(id: String): Job = command { connection ->
        api.restartWorker(connection, id)
    }

    fun startOAuth(accountId: String): Job = viewModelScope.launch {
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            val connection = connectionProvider()
            val started = api.startOAuth(connection, accountId)
            loadSnapshot(connection)
            mutableEvents.emit(FactoryUiEvent.OpenExternalUrl(started.authorizationUrl))

            repeat(oauthMaxAttempts) {
                if (oauthPollDelayMs > 0) delay(oauthPollDelayMs)
                val account = api.oauthStatus(connection, accountId)
                if (account.stage != "ACP_CONNECTING") {
                    loadSnapshot(connection)
                    return@launch
                }
            }

            loadSnapshot(connection)
            mutableState.value = mutableState.value.copy(
                loading = false,
                error = "Threads OAuth vẫn đang chờ xác nhận. Có thể làm mới lại trạng thái sau.",
            )
        } catch (error: Exception) {
            mutableState.value = mutableState.value.copy(
                loading = false,
                error = error.message?.take(300) ?: "Không thể bắt đầu Threads OAuth",
            )
        }
    }

    private fun command(action: suspend (FactoryConnection) -> Unit): Job = viewModelScope.launch {
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            val connection = connectionProvider()
            action(connection)
            loadSnapshot(connection)
        } catch (error: Exception) {
            mutableState.value = mutableState.value.copy(
                loading = false,
                error = error.message?.take(300) ?: "Không thể thực hiện lệnh trên Controller",
            )
        }
    }

    private suspend fun loadSnapshot(connection: FactoryConnection = connectionProvider()) {
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            val dashboard = api.dashboard(connection)
            val accounts = api.accounts(connection)
            val checkpoints = api.checkpoints(connection)
            val workers = api.workers(connection)
            val runners = try {
                api.runners(connection)
            } catch (_: Exception) {
                workers.map { worker ->
                    FactoryRunnerDto(
                        id = worker.id,
                        runnerType = worker.runnerType,
                        deviceId = worker.deviceId,
                        deviceName = worker.deviceName,
                        avdName = worker.avdName,
                        state = worker.state,
                        currentAccountId = worker.currentAccountId,
                        currentJobId = worker.currentJobId,
                        lastHeartbeatAt = worker.lastHeartbeatAt,
                        draining = worker.draining,
                        lastError = worker.lastError,
                    )
                }
            }
            mutableState.value = FactoryUiState(
                dashboard = dashboard,
                accounts = accounts,
                checkpoints = checkpoints,
                workers = workers,
                runners = runners,
                loading = false,
                error = null,
            )
        } catch (error: Exception) {
            mutableState.value = mutableState.value.copy(
                loading = false,
                error = error.message?.take(300) ?: "Không thể tải trạng thái Controller",
            )
        }
    }
}
