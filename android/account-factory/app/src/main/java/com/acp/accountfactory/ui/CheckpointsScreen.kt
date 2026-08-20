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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun CheckpointsScreen(
    state: FactoryUiState,
    onBack: () -> Unit,
    onContinue: (String) -> Unit,
    onRetry: (String) -> Unit,
    onSnooze: (String, Int) -> Unit,
    onStopAccount: (String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Human Checkpoints") },
                navigationIcon = { TextButton(onClick = onBack) { Text("‹ Back") } },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(state.checkpoints, key = { it.id }) { checkpoint ->
                val enabled = checkpointActionsEnabled(checkpoint.status)
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                        Text("${checkpoint.type} • ${checkpoint.status}")
                        Text(checkpoint.message ?: "Cần xác nhận thủ công")
                        Text("Account ${checkpoint.accountId} • Worker ${checkpoint.workerId ?: "—"}")
                        Text("Đã chờ ${waitingDuration(checkpoint.createdAt)}")
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(
                                enabled = enabled,
                                onClick = { onContinue(checkpoint.id) },
                                modifier = Modifier.weight(1f),
                            ) { Text("CONTINUE") }
                            OutlinedButton(
                                enabled = enabled,
                                onClick = { onRetry(checkpoint.id) },
                                modifier = Modifier.weight(1f),
                            ) { Text("RETRY") }
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            listOf(10, 30, 60).forEach { minutes ->
                                TextButton(
                                    enabled = enabled,
                                    onClick = { onSnooze(checkpoint.id, minutes) },
                                ) { Text("SNOOZE $minutes") }
                            }
                        }
                        OutlinedButton(
                            enabled = checkpoint.status != "RESOLVED",
                            onClick = { onStopAccount(checkpoint.accountId) },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("STOP ACCOUNT") }
                    }
                }
            }
        }
    }
}
