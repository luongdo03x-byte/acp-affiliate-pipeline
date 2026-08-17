package com.acp.accountfactory

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.acp.accountfactory.data.AccountEntity
import com.acp.accountfactory.data.FactoryDatabase
import com.acp.accountfactory.data.FactoryRepository
import com.acp.accountfactory.domain.AccountStage
import com.acp.accountfactory.network.AcpApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val repo = FactoryRepository(FactoryDatabase.get(this).dao())
        setContent { MaterialTheme { FactoryApp(repo) } }
    }
}

private enum class Screen { DASHBOARD, WORKFLOW, ACCOUNTS }
private enum class AccountFilter { ALL, NEED_IG, NEED_THREADS, NEED_ACP, ACTIVE, ERROR }

private class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("factory_settings", Context.MODE_PRIVATE)
    var baseUrl: String
        get() = prefs.getString("base_url", "") ?: ""
        set(value) = prefs.edit().putString("base_url", value.trim()).apply()
    var factoryKey: String
        get() = prefs.getString("factory_key", "") ?: ""
        set(value) = prefs.edit().putString("factory_key", value.trim()).apply()
}

@Composable
private fun FactoryApp(repo: FactoryRepository) {
    val context = LocalContext.current
    val settings = remember { SettingsStore(context) }
    val api = remember { AcpApi() }
    val scope = rememberCoroutineScope()
    val batch by repo.latestBatch().collectAsState(initial = null)
    val accountsFlow = remember(batch?.id) { batch?.let { repo.accounts(it.id) } ?: flowOf(emptyList()) }
    val accounts by accountsFlow.collectAsState(initial = emptyList())
    var screen by remember { mutableStateOf(Screen.DASHBOARD) }
    var selectedId by remember { mutableStateOf<String?>(null) }
    var showSettings by remember { mutableStateOf(false) }

    val selected = accounts.firstOrNull { it.id == selectedId }
    if (showSettings) {
        SettingsDialog(settings = settings, onClose = { showSettings = false })
    }

    when (screen) {
        Screen.DASHBOARD -> DashboardScreen(
            batchName = batch?.name,
            accounts = accounts,
            onCreateBatch = { scope.launch { repo.createBatch(count = 50, prefix = "acp") } },
            onContinue = {
                val next = accounts.firstOrNull { it.stage != AccountStage.ACP_ACTIVE } ?: accounts.lastOrNull()
                selectedId = next?.id
                if (next != null) screen = Screen.WORKFLOW
            },
            onAccounts = { screen = Screen.ACCOUNTS },
            onSettings = { showSettings = true },
        )
        Screen.ACCOUNTS -> AccountsScreen(
            accounts = accounts,
            onBack = { screen = Screen.DASHBOARD },
            onSelect = { selectedId = it.id; screen = Screen.WORKFLOW },
        )
        Screen.WORKFLOW -> if (selected != null && batch != null) {
            WorkflowScreen(
                account = selected,
                batchId = batch!!.id,
                settings = settings,
                api = api,
                repo = repo,
                onBack = { screen = Screen.DASHBOARD },
                onNext = {
                    val next = accounts.firstOrNull { it.sequence > selected.sequence }
                    if (next != null) selectedId = next.id else screen = Screen.DASHBOARD
                },
            )
        } else {
            screen = Screen.DASHBOARD
        }
    }
}

@Composable
private fun DashboardScreen(
    batchName: String?,
    accounts: List<AccountEntity>,
    onCreateBatch: () -> Unit,
    onContinue: () -> Unit,
    onAccounts: () -> Unit,
    onSettings: () -> Unit,
) {
    val ig = accounts.count { it.stage != AccountStage.PLANNED && it.stage != AccountStage.NEEDS_VERIFICATION }
    val threads = accounts.count { it.stage in setOf(AccountStage.THREADS_CREATED, AccountStage.ACP_CONNECTING, AccountStage.ACP_ACTIVE, AccountStage.ERROR) }
    val active = accounts.count { it.stage == AccountStage.ACP_ACTIVE }
    Scaffold(topBar = { TopAppBar(title = { Text("ACP Account Factory") }, actions = { TextButton(onClick = onSettings) { Text("Cài đặt") } }) }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (batchName == null) {
                Text("Chưa có batch", style = MaterialTheme.typography.headlineSmall)
                Text("Tạo batch P0 gồm 50 profile, tự chia 10 nhóm × 5.")
                Button(onClick = onCreateBatch) { Text("TẠO BATCH 50") }
            } else {
                Text(batchName, style = MaterialTheme.typography.headlineSmall)
                Text("Tổng: ${accounts.size}")
                Text("Instagram: $ig / ${accounts.size}")
                Text("Threads: $threads / ${accounts.size}")
                Text("ACP Active: $active / ${accounts.size}")
                Button(onClick = onContinue, enabled = accounts.isNotEmpty(), modifier = Modifier.fillMaxWidth()) { Text("CONTINUE") }
                OutlinedButton(onClick = onAccounts, modifier = Modifier.fillMaxWidth()) { Text("ALL ACCOUNTS") }
            }
        }
    }
}

@Composable
private fun AccountsScreen(accounts: List<AccountEntity>, onBack: () -> Unit, onSelect: (AccountEntity) -> Unit) {
    var filter by remember { mutableStateOf(AccountFilter.ALL) }
    val filtered = accounts.filter {
        when (filter) {
            AccountFilter.ALL -> true
            AccountFilter.NEED_IG -> it.stage == AccountStage.PLANNED || it.stage == AccountStage.NEEDS_VERIFICATION
            AccountFilter.NEED_THREADS -> it.stage == AccountStage.IG_CREATED
            AccountFilter.NEED_ACP -> it.stage == AccountStage.THREADS_CREATED || it.stage == AccountStage.ACP_CONNECTING
            AccountFilter.ACTIVE -> it.stage == AccountStage.ACP_ACTIVE
            AccountFilter.ERROR -> it.stage == AccountStage.ERROR
        }
    }
    Scaffold(topBar = { TopAppBar(title = { Text("All Accounts") }, navigationIcon = { TextButton(onClick = onBack) { Text("‹ Back") } }) }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(horizontal = 12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf(AccountFilter.ALL, AccountFilter.NEED_IG, AccountFilter.NEED_THREADS, AccountFilter.NEED_ACP).forEach { f ->
                    FilterChip(selected = filter == f, onClick = { filter = f }, label = { Text(f.name) })
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf(AccountFilter.ACTIVE, AccountFilter.ERROR).forEach { f ->
                    FilterChip(selected = filter == f, onClick = { filter = f }, label = { Text(f.name) })
                }
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(filtered, key = { it.id }) { account ->
                    Card(onClick = { onSelect(account) }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp)) {
                            Text("#${account.sequence.toString().padStart(2, '0')}  @${account.username}")
                            Text("Group ${account.groupNo} • ${account.stage.name}", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun WorkflowScreen(
    account: AccountEntity,
    batchId: String,
    settings: SettingsStore,
    api: AcpApi,
    repo: FactoryRepository,
    onBack: () -> Unit,
    onNext: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var uiError by remember { mutableStateOf<String?>(null) }

    fun open(url: String) = context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    fun copy(label: String, value: String) {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText(label, value))
    }

    LaunchedEffect(account.stage, account.oauthSessionId, settings.baseUrl, settings.factoryKey) {
        if (account.stage == AccountStage.ACP_CONNECTING && !account.oauthSessionId.isNullOrBlank()
            && settings.baseUrl.isNotBlank() && settings.factoryKey.isNotBlank()) {
            repeat(120) {
                delay(3000)
                try {
                    val status = api.status(settings.baseUrl, settings.factoryKey, account.oauthSessionId)
                    when (status.status) {
                        "ACTIVE" -> {
                            repo.setActive(account.id, status.threadsUserId, status.channelCode)
                            return@LaunchedEffect
                        }
                        "ACCOUNT_MISMATCH", "OAUTH_ERROR", "SESSION_EXPIRED" -> {
                            repo.setOauthError(account.id, status.error ?: status.status)
                            return@LaunchedEffect
                        }
                    }
                } catch (_: Exception) {
                    // Network may disappear while browser OAuth is open. Keep polling.
                }
            }
        }
    }

    Scaffold(topBar = { TopAppBar(title = { Text("Account ${account.sequence} / 50") }, navigationIcon = { TextButton(onClick = onBack) { Text("‹ Batch") } }) }) { pad ->
        LazyColumn(Modifier.fillMaxSize().padding(pad).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Text("@${account.username}", style = MaterialTheme.typography.headlineSmall)
                Text("Group ${account.groupNo} • ${account.stage.name}")
                if (account.channelCode != null) Text("ACP: ${account.channelCode}")
                val err = account.lastError ?: uiError
                if (err != null) Text(err, color = MaterialTheme.colorScheme.error)
            }
            item { CopyCard("Username", account.username) { copy("username", account.username) } }
            item { CopyCard("Display name", account.displayName) { copy("display", account.displayName) } }
            item { CopyCard("Bio", account.bio) { copy("bio", account.bio) } }
            item {
                when (account.stage) {
                    AccountStage.PLANNED, AccountStage.NEEDS_VERIFICATION -> {
                        Button(onClick = { open("https://www.instagram.com/") }, modifier = Modifier.fillMaxWidth()) { Text("OPEN INSTAGRAM") }
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(onClick = { scope.launch { repo.transition(account.id, AccountStage.IG_CREATED) } }, modifier = Modifier.fillMaxWidth()) { Text("MARK IG CREATED") }
                    }
                    AccountStage.IG_CREATED -> {
                        Button(onClick = { open("https://www.threads.net/") }, modifier = Modifier.fillMaxWidth()) { Text("OPEN THREADS") }
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(onClick = { scope.launch { repo.transition(account.id, AccountStage.THREADS_CREATED) } }, modifier = Modifier.fillMaxWidth()) { Text("MARK THREADS CREATED") }
                    }
                    AccountStage.THREADS_CREATED -> {
                        Button(
                            enabled = !busy,
                            onClick = {
                                if (settings.baseUrl.isBlank() || settings.factoryKey.isBlank()) {
                                    uiError = "Mở Cài đặt và nhập ACP Base URL + Factory Key trước."
                                } else scope.launch {
                                    busy = true; uiError = null
                                    try {
                                        val started = api.start(settings.baseUrl, settings.factoryKey, account.username, batchId, account.id)
                                        repo.setConnecting(account.id, started.sessionId)
                                        open(started.authorizationUrl)
                                    } catch (e: Exception) {
                                        uiError = e.message ?: "Không kết nối được ACP"
                                    } finally { busy = false }
                                }
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (busy) "CONNECTING..." else "CONNECT ACP") }
                    }
                    AccountStage.ACP_CONNECTING -> Text("Đang chờ Threads OAuth. Sau khi authorize, app tự kiểm tra trạng thái ACP.")
                    AccountStage.ACP_ACTIVE -> {
                        Text("✓ ACP ACTIVE")
                        Button(onClick = onNext, modifier = Modifier.fillMaxWidth()) { Text("NEXT ACCOUNT") }
                    }
                    AccountStage.ERROR -> {
                        OutlinedButton(onClick = { scope.launch { repo.transition(account.id, AccountStage.THREADS_CREATED) } }, modifier = Modifier.fillMaxWidth()) { Text("TRY AGAIN") }
                    }
                }
            }
        }
    }
}

@Composable
private fun CopyCard(label: String, value: String, onCopy: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f)) { Text(label, style = MaterialTheme.typography.labelMedium); Text(value) }
            TextButton(onClick = onCopy) { Text("COPY") }
        }
    }
}

@Composable
private fun SettingsDialog(settings: SettingsStore, onClose: () -> Unit) {
    var baseUrl by remember { mutableStateOf(settings.baseUrl) }
    var key by remember { mutableStateOf(settings.factoryKey) }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Kết nối ACP") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(baseUrl, { baseUrl = it }, label = { Text("ACP Base URL") }, placeholder = { Text("https://acp.example.com") })
                OutlinedTextField(key, { key = it }, label = { Text("Factory Key") })
                Text("App không lưu Threads access token, App Secret, ACP_MASTER_KEY hoặc mật khẩu tài khoản.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { Button(onClick = { settings.baseUrl = baseUrl; settings.factoryKey = key; onClose() }) { Text("LƯU") } },
        dismissButton = { TextButton(onClick = onClose) { Text("HỦY") } },
    )
}
