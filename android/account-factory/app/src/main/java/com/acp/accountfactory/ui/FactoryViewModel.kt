package com.acp.accountfactory.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.acp.accountfactory.network.DashboardDto
import com.acp.accountfactory.network.FactoryAccountDto
import com.acp.accountfactory.network.FactoryCheckpointDto
import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryV2ApiClient
import com.acp.accountfactory.network.FactoryWorkerDto
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class FactoryUiState(
    val dashboard: DashboardDto? = null,
    val accounts: List<FactoryAccountDto> = emptyList(),
    val checkpoints: List<FactoryCheckpointDto> = emptyList(),
    val workers: List<FactoryWorkerDto> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
)

class FactoryViewModel(
    private val api: FactoryV2ApiClient,
    private val connectionProvider: () -> FactoryConnection,
) : ViewModel() {
    private val mutableState = MutableStateFlow(FactoryUiState())
    val state: StateFlow<FactoryUiState> = mutableState.asStateFlow()

    fun refresh(): Job = viewModelScope.launch {
        loadSnapshot()
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
            mutableState.value = FactoryUiState(
                dashboard = dashboard,
                accounts = accounts,
                checkpoints = checkpoints,
                workers = workers,
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
