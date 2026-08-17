package com.acp.accountfactory

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.acp.accountfactory.network.FactoryConnection
import com.acp.accountfactory.network.FactoryV2Api
import com.acp.accountfactory.runner.AccessibilityReadiness
import com.acp.accountfactory.runner.LocalDeviceActions
import com.acp.accountfactory.runner.LocalDeviceRunner
import com.acp.accountfactory.runner.LocalRunnerIdentityStore
import com.acp.accountfactory.ui.AccountsScreen
import com.acp.accountfactory.ui.CheckpointsScreen
import com.acp.accountfactory.ui.CreateAccountScreen
import com.acp.accountfactory.ui.DashboardScreen
import com.acp.accountfactory.ui.ExecutionTarget
import com.acp.accountfactory.ui.FactoryUiEvent
import com.acp.accountfactory.ui.FactoryViewModel
import com.acp.accountfactory.ui.WorkersScreen

class MainActivity : ComponentActivity() {
    private lateinit var settingsStore: SettingsStore
    private lateinit var api: FactoryV2Api
    private lateinit var identityStore: LocalRunnerIdentityStore
    private lateinit var localRunner: LocalDeviceRunner

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settingsStore = SettingsStore(this)
        api = FactoryV2Api()
        identityStore = LocalRunnerIdentityStore(this)
        localRunner = LocalDeviceRunner(
            api = api,
            connectionProvider = { settingsStore.connection() },
            identityStore = identityStore,
            actions = LocalDeviceActions(this),
        )
        if (settingsStore.isConfigured()) localRunner.start()
        val localDeviceId = identityStore.getOrCreate().deviceId

        setContent {
            MaterialTheme {
                FactoryApp(
                    settings = settingsStore,
                    api = api,
                    localDeviceId = localDeviceId,
                    onControllerConfigured = { localRunner.start() },
                )
            }
        }
    }

    override fun onDestroy() {
        localRunner.stop()
        super.onDestroy()
    }
}

private enum class Screen { DASHBOARD, CREATE_ACCOUNT, ACCOUNTS, CHECKPOINTS, WORKERS }

private class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("factory_settings", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = prefs.getString("base_url", "") ?: ""
        set(value) = prefs.edit().putString("base_url", value.trim()).apply()

    var factoryKey: String
        get() = prefs.getString("factory_key", "") ?: ""
        set(value) = prefs.edit().putString("factory_key", value.trim()).apply()

    fun connection(): FactoryConnection = FactoryConnection(baseUrl, factoryKey)
    fun isConfigured(): Boolean = baseUrl.isNotBlank() && factoryKey.isNotBlank()
}

@Composable
private fun FactoryApp(
    settings: SettingsStore,
    api: FactoryV2Api,
    localDeviceId: String,
    onControllerConfigured: () -> Unit,
) {
    val context = LocalContext.current
    val viewModel = remember(api, settings) { FactoryViewModel(api) { settings.connection() } }
    val state by viewModel.state.collectAsState()
    var screen by remember { mutableStateOf(Screen.DASHBOARD) }
    var showSettings by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        if (settings.isConfigured()) viewModel.refresh()
    }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is FactoryUiEvent.OpenExternalUrl -> {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(event.url)))
                }
            }
        }
    }

    if (showSettings) {
        SettingsDialog(
            settings = settings,
            onClose = { showSettings = false },
            onSaved = {
                showSettings = false
                if (settings.isConfigured()) {
                    onControllerConfigured()
                    viewModel.refresh()
                }
            },
        )
    }

    when (screen) {
        Screen.DASHBOARD -> DashboardScreen(
            state = state,
            onRefresh = {
                if (settings.isConfigured()) viewModel.refresh() else showSettings = true
            },
            onCreateAccount = {
                if (!settings.isConfigured()) {
                    showSettings = true
                } else {
                    viewModel.refresh()
                    screen = Screen.CREATE_ACCOUNT
                }
            },
            onAccounts = { screen = Screen.ACCOUNTS },
            onCheckpoints = { screen = Screen.CHECKPOINTS },
            onWorkers = { screen = Screen.WORKERS },
            onPause = viewModel::pauseBatch,
            onResume = viewModel::resumeBatch,
            onSettings = { showSettings = true },
        )

        Screen.CREATE_ACCOUNT -> CreateAccountScreen(
            state = state,
            localDeviceId = localDeviceId,
            onBack = { screen = Screen.DASHBOARD },
            onCreate = { target ->
                if (target is ExecutionTarget.ThisPhone && !AccessibilityReadiness.isEnabled(context)) {
                    context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                } else {
                    viewModel.createAccount(target.controllerValue)
                    screen = Screen.DASHBOARD
                }
            },
        )

        Screen.ACCOUNTS -> AccountsScreen(
            state = state,
            onBack = { screen = Screen.DASHBOARD },
            onRetry = viewModel::retryAccount,
            onStop = viewModel::stopAccount,
        )

        Screen.CHECKPOINTS -> CheckpointsScreen(
            state = state,
            onBack = { screen = Screen.DASHBOARD },
            onContinue = viewModel::continueCheckpoint,
            onRetry = viewModel::retryCheckpoint,
            onSnooze = viewModel::snoozeCheckpoint,
            onStopAccount = viewModel::stopAccount,
        )

        Screen.WORKERS -> WorkersScreen(
            state = state,
            onBack = { screen = Screen.DASHBOARD },
            onDrain = viewModel::drainWorker,
            onRestart = viewModel::restartWorker,
        )
    }
}

@Composable
private fun SettingsDialog(
    settings: SettingsStore,
    onClose: () -> Unit,
    onSaved: () -> Unit,
) {
    var baseUrl by remember { mutableStateOf(settings.baseUrl) }
    var factoryKey by remember { mutableStateOf(settings.factoryKey) }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Kết nối Controller") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = baseUrl,
                    onValueChange = { baseUrl = it },
                    label = { Text("Factory Controller URL") },
                    placeholder = { Text("http://192.168.68.71:5001") },
                    singleLine = true,
                )
                OutlinedTextField(
                    value = factoryKey,
                    onValueChange = { factoryKey = it },
                    label = { Text("Factory Key") },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                )
                Text("Cấu hình này được lưu trên máy. App không lưu Threads token, mật khẩu, OTP/CAPTCHA, App Secret hoặc ACP_MASTER_KEY.")
            }
        },
        confirmButton = {
            Button(onClick = {
                settings.baseUrl = baseUrl
                settings.factoryKey = factoryKey
                onSaved()
            }) { Text("LƯU") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("HỦY") } },
    )
}
