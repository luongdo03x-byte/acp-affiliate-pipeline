package com.acp.accountfactory.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun AccountsScreen(
    state: FactoryUiState,
    onBack: () -> Unit,
    onRetry: (String) -> Unit,
    onStop: (String) -> Unit,
    onConnectOAuth: (String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Accounts") },
                navigationIcon = { TextButton(onClick = onBack) { Text("‹ Back") } },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(12.dp),
        ) {
            items(state.accounts, key = { it.id }) { account ->
                Card(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
                    Column(Modifier.padding(14.dp)) {
                        Text("#${account.sequence} @${account.username}")
                        Text(account.displayName)
                        Text("${account.stage} • safe ${account.lastSafeStage}")
                        account.channelCode?.let { Text("ACP: $it") }
                        account.lastErrorMessage?.let { Text("Lỗi: $it") }

                        when (account.stage) {
                            "THREADS_CREATED" -> Button(
                                onClick = { onConnectOAuth(account.id) },
                                modifier = Modifier.fillMaxWidth(),
                            ) { Text("CONNECT ACP") }
                            "ACP_CONNECTING" -> Text("Đang chờ Threads OAuth…")
                            "ACP_ACTIVE" -> Text("✓ ACP ACTIVE")
                        }

                        if (account.stage in setOf("ERROR", "RETRY_PENDING", "NEEDS_CONFIRMATION")) {
                            TextButton(onClick = { onRetry(account.id) }) { Text("RETRY") }
                        }
                        if (account.stage !in setOf("ACP_ACTIVE", "DISABLED")) {
                            TextButton(onClick = { onStop(account.id) }) { Text("STOP ACCOUNT") }
                        }
                    }
                }
            }
        }
    }
}
