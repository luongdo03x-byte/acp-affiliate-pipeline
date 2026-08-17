package com.acp.accountfactory.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun DashboardScreen(
    state: FactoryUiState,
    onRefresh: () -> Unit,
    onCreateAccount: () -> Unit,
    onAccounts: () -> Unit,
    onCheckpoints: () -> Unit,
    onWorkers: () -> Unit,
    onPause: (String) -> Unit,
    onResume: (String) -> Unit,
    onSettings: () -> Unit,
) {
    val dashboard = state.dashboard
    val batch = dashboard?.batch
    val actionable = state.checkpoints.filter { checkpointActionsEnabled(it.status) }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("ACP Account Factory V2") },
                actions = {
                    TextButton(onClick = onRefresh) { Text("Làm mới") }
                    TextButton(onClick = onSettings) { Text("Cài đặt") }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (state.error != null) {
                item { Text(state.error, color = MaterialTheme.colorScheme.error) }
            }
            item {
                Button(onClick = onCreateAccount, modifier = Modifier.fillMaxWidth()) {
                    Text("+ TẠO ACCOUNT")
                }
            }
            if (dashboard == null) {
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(if (state.loading) "Đang tải Controller…" else "Chưa có dữ liệu Controller")
                            Text("Nhập ACP Base URL + Factory Key rồi bấm Làm mới.")
                        }
                    }
                }
            } else {
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(batch?.name ?: "Chưa có batch", style = MaterialTheme.typography.titleLarge)
                            Text("Trạng thái: ${batch?.status ?: "—"}")
                            Text("Active ${dashboard.accounts.active}/${dashboard.accounts.total}")
                            Text("Running ${dashboard.accounts.running} • Chờ người dùng ${dashboard.accounts.waitingHuman}")
                            Text("Queue ${dashboard.accounts.queued} • Error ${dashboard.accounts.error}")
                            Text("Runners ${dashboard.workers.total} • Running ${dashboard.workers.running} • Waiting ${dashboard.workers.waitingHuman}")
                            dashboard.host?.let { host ->
                                Text("Host AVD: CPU ${host.cpuPercent.toInt()}% • RAM trống ${host.ramAvailableMb} MB • ${capacityLabel(host.capacityState)}")
                            }
                        }
                    }
                }
                if (batch != null) {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (batch.status == "PAUSED") {
                                Button(onClick = { onResume(batch.id) }, modifier = Modifier.weight(1f)) { Text("RESUME") }
                            } else {
                                OutlinedButton(onClick = { onPause(batch.id) }, modifier = Modifier.weight(1f)) { Text("PAUSE") }
                            }
                            OutlinedButton(onClick = onAccounts, modifier = Modifier.weight(1f)) { Text("ACCOUNTS") }
                        }
                    }
                }
                item {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = onCheckpoints, modifier = Modifier.weight(1f)) { Text("CHECKPOINTS (${actionable.size})") }
                        OutlinedButton(onClick = onWorkers, modifier = Modifier.weight(1f)) { Text("RUNNERS") }
                    }
                }
                if (actionable.isNotEmpty()) {
                    item { Text("Cần xử lý", style = MaterialTheme.typography.titleMedium) }
                    items(actionable.take(5), key = { it.id }) { checkpoint ->
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(14.dp)) {
                                Text(checkpoint.type)
                                Text(checkpoint.message ?: "Cần xác nhận thủ công")
                                Text("Đã chờ ${waitingDuration(checkpoint.createdAt)}", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
        }
    }
}
