package com.acp.accountfactory.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun WorkersScreen(
    state: FactoryUiState,
    onBack: () -> Unit,
    onDrain: (String) -> Unit,
    onRestart: (String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Runners") },
                navigationIcon = { TextButton(onClick = onBack) { Text("‹ Back") } },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(state.workers, key = { it.id }) { worker ->
                val label = when (worker.runnerType) {
                    "LOCAL_DEVICE" -> worker.deviceName ?: "This phone"
                    else -> worker.avdName ?: worker.id
                }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                        Text(label)
                        Text("${worker.runnerType} • ${worker.state}${if (worker.draining) " • draining" else ""}")
                        Text("Processed ${worker.processedCount} • Recovery ${worker.recoveryCount}")
                        worker.currentRamMb?.let { Text("RAM $it MB") }
                        worker.currentCpuPercent?.let { Text("CPU ${it.toInt()}%") }
                        worker.lastError?.let { Text("Lỗi: $it") }
                        if (worker.runnerType == "REMOTE_AVD") {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedButton(
                                    enabled = worker.state !in setOf("WAITING_HUMAN", "STOPPED", "ERROR") && !worker.draining,
                                    onClick = { onDrain(worker.id) },
                                    modifier = Modifier.weight(1f),
                                ) { Text("DRAIN") }
                                OutlinedButton(
                                    enabled = worker.state !in setOf("WAITING_HUMAN", "STOPPED") && worker.currentJobId == null,
                                    onClick = { onRestart(worker.id) },
                                    modifier = Modifier.weight(1f),
                                ) { Text("RESTART") }
                            }
                        }
                    }
                }
            }
        }
    }
}
