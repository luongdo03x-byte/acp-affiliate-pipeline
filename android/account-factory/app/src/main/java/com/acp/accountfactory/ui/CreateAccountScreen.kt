package com.acp.accountfactory.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.acp.accountfactory.network.FactoryRunnerDto

sealed interface ExecutionTarget {
    val value: String
    val controllerValue: String
    val label: String

    data class ThisPhone(
        val workerId: String,
        override val label: String,
    ) : ExecutionTarget {
        override val value = "THIS_PHONE"
        override val controllerValue = workerId
    }

    data object AutoAvd : ExecutionTarget {
        override val value = "AUTO_AVD"
        override val controllerValue = "AUTO_AVD"
        override val label = "Auto-select AVD"
    }

    data class SpecificAvd(
        val workerId: String,
        override val label: String,
    ) : ExecutionTarget {
        override val value = workerId
        override val controllerValue = workerId
    }
}

fun buildExecutionTargets(
    localDeviceId: String,
    runners: List<FactoryRunnerDto>,
): List<ExecutionTarget> {
    val result = mutableListOf<ExecutionTarget>()
    runners.firstOrNull {
        it.runnerType == "LOCAL_DEVICE" &&
            it.deviceId == localDeviceId &&
            it.state == "READY" &&
            !it.draining
    }?.let { local ->
        result += ExecutionTarget.ThisPhone(
            workerId = local.id,
            label = "This phone (${local.deviceName ?: "Android"})",
        )
    }
    result += ExecutionTarget.AutoAvd
    result += runners
        .filter {
            it.runnerType == "REMOTE_AVD" &&
                it.state == "READY" &&
                !it.draining
        }
        .sortedBy { it.avdName ?: it.id }
        .map { avd ->
            ExecutionTarget.SpecificAvd(
                workerId = avd.id,
                label = avd.avdName ?: avd.id,
            )
        }
    return result
}

@Composable
fun CreateAccountScreen(
    state: FactoryUiState,
    localDeviceId: String,
    onBack: () -> Unit,
    onCreate: (ExecutionTarget) -> Unit,
) {
    val options = buildExecutionTargets(localDeviceId, state.runners)
    var selectedValue by remember(options) {
        mutableStateOf(options.firstOrNull()?.value ?: "AUTO_AVD")
    }
    val selected = options.firstOrNull { it.value == selectedValue }
        ?: options.firstOrNull()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Create Account") },
                navigationIcon = { TextButton(onClick = onBack) { Text("←") } },
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Run on")
            options.forEach { option ->
                Row(Modifier.fillMaxWidth()) {
                    RadioButton(
                        selected = option.value == selected?.value,
                        onClick = { selectedValue = option.value },
                    )
                    Text(option.label, modifier = Modifier.padding(top = 12.dp))
                }
            }
            if (options.none { it is ExecutionTarget.ThisPhone }) {
                Text("This phone chưa READY. Kiểm tra kết nối Controller và Accessibility observer.")
            }
            Button(
                onClick = { selected?.let(onCreate) },
                enabled = selected != null && !state.loading,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (state.loading) "Đang tạo…" else "BẮT ĐẦU")
            }
        }
    }
}
