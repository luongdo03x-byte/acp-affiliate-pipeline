package com.acp.accountfactory.network

import org.json.JSONArray
import org.json.JSONObject

data class BatchSummaryDto(
    val id: String,
    val name: String?,
    val status: String,
    val targetCount: Int,
)

data class AccountCountsDto(
    val total: Int,
    val active: Int,
    val running: Int,
    val waitingHuman: Int,
    val error: Int,
    val queued: Int,
)

data class WorkerCountsDto(
    val total: Int,
    val running: Int,
    val waitingHuman: Int,
    val starting: Int,
)

data class HostStatusDto(
    val cpuPercent: Double,
    val ramAvailableMb: Int,
    val swapUsedMb: Int,
    val capacityState: String,
)

data class DashboardDto(
    val batch: BatchSummaryDto?,
    val accounts: AccountCountsDto,
    val workers: WorkerCountsDto,
    val host: HostStatusDto?,
)

data class FactoryAccountDto(
    val id: String,
    val batchId: String,
    val sequence: Int,
    val groupNo: Int,
    val username: String,
    val displayName: String,
    val bio: String?,
    val stage: String,
    val lastSafeStage: String,
    val assignedWorkerId: String?,
    val currentJobId: String?,
    val channelCode: String?,
    val lastErrorCode: String?,
    val lastErrorMessage: String?,
)

data class FactoryWorkerDto(
    val id: String,
    val avdName: String,
    val state: String,
    val currentAccountId: String?,
    val currentJobId: String?,
    val lastHeartbeatAt: String?,
    val lastProgressAt: String?,
    val processedCount: Int,
    val recoveryCount: Int,
    val currentRamMb: Int?,
    val currentCpuPercent: Double?,
    val draining: Boolean,
    val lastError: String?,
)

data class FactoryCheckpointDto(
    val id: String,
    val batchId: String,
    val accountId: String,
    val workerId: String?,
    val type: String,
    val status: String,
    val message: String?,
    val createdAt: String,
    val nextReminderAt: String?,
    val snoozedUntil: String?,
)

data class CommandAcceptedDto(
    val commandId: String,
    val status: String,
)

object FactoryV2Json {
    private fun JSONObject.stringOrNull(name: String): String? =
        optString(name).takeIf { it.isNotBlank() && it != "null" }

    fun parseDashboard(text: String): DashboardDto {
        val root = JSONObject(text)
        val batchJson = root.optJSONObject("batch")
        val accounts = root.getJSONObject("accounts")
        val workers = root.getJSONObject("workers")
        val hostJson = root.optJSONObject("host")
        return DashboardDto(
            batch = batchJson?.let {
                BatchSummaryDto(
                    id = it.getString("id"),
                    name = it.stringOrNull("name"),
                    status = it.getString("status"),
                    targetCount = it.getInt("target_count"),
                )
            },
            accounts = AccountCountsDto(
                total = accounts.getInt("total"),
                active = accounts.getInt("active"),
                running = accounts.getInt("running"),
                waitingHuman = accounts.getInt("waiting_human"),
                error = accounts.getInt("error"),
                queued = accounts.getInt("queued"),
            ),
            workers = WorkerCountsDto(
                total = workers.getInt("total"),
                running = workers.getInt("running"),
                waitingHuman = workers.getInt("waiting_human"),
                starting = workers.getInt("starting"),
            ),
            host = hostJson?.let {
                HostStatusDto(
                    cpuPercent = it.optDouble("cpu_percent", 0.0),
                    ramAvailableMb = it.optInt("ram_available_mb", 0),
                    swapUsedMb = it.optInt("swap_used_mb", 0),
                    capacityState = it.optString("capacity_state", "UNKNOWN"),
                )
            },
        )
    }

    fun parseAccounts(text: String): List<FactoryAccountDto> {
        val array = JSONObject(text).getJSONArray("accounts")
        return List(array.length()) { index -> parseAccount(array.getJSONObject(index)) }
    }

    fun parseWorkers(text: String): List<FactoryWorkerDto> {
        val array = JSONObject(text).getJSONArray("workers")
        return List(array.length()) { index ->
            val row = array.getJSONObject(index)
            FactoryWorkerDto(
                id = row.getString("id"),
                avdName = row.getString("avd_name"),
                state = row.getString("state"),
                currentAccountId = row.stringOrNull("current_account_id"),
                currentJobId = row.stringOrNull("current_job_id"),
                lastHeartbeatAt = row.stringOrNull("last_heartbeat_at"),
                lastProgressAt = row.stringOrNull("last_progress_at"),
                processedCount = row.optInt("processed_count", 0),
                recoveryCount = row.optInt("recovery_count", 0),
                currentRamMb = row.optInt("current_ram_mb").takeIf { !row.isNull("current_ram_mb") },
                currentCpuPercent = row.optDouble("current_cpu_percent").takeIf { !row.isNull("current_cpu_percent") },
                draining = row.optInt("draining", 0) != 0,
                lastError = row.stringOrNull("last_error"),
            )
        }
    }

    fun parseCheckpoints(text: String): List<FactoryCheckpointDto> {
        val array = JSONObject(text).getJSONArray("checkpoints")
        return List(array.length()) { index ->
            val row = array.getJSONObject(index)
            FactoryCheckpointDto(
                id = row.getString("id"),
                batchId = row.getString("batch_id"),
                accountId = row.getString("account_id"),
                workerId = row.stringOrNull("worker_id"),
                type = row.getString("type"),
                status = row.getString("status"),
                message = row.stringOrNull("message"),
                createdAt = row.getString("created_at"),
                nextReminderAt = row.stringOrNull("next_reminder_at"),
                snoozedUntil = row.stringOrNull("snoozed_until"),
            )
        }
    }

    fun parseCommand(text: String): CommandAcceptedDto {
        val root = JSONObject(text)
        return CommandAcceptedDto(
            commandId = root.getString("command_id"),
            status = root.getString("status"),
        )
    }

    private fun parseAccount(row: JSONObject) = FactoryAccountDto(
        id = row.getString("id"),
        batchId = row.getString("batch_id"),
        sequence = row.getInt("sequence"),
        groupNo = row.getInt("group_no"),
        username = row.getString("username"),
        displayName = row.getString("display_name"),
        bio = row.stringOrNull("bio"),
        stage = row.getString("stage"),
        lastSafeStage = row.getString("last_safe_stage"),
        assignedWorkerId = row.stringOrNull("assigned_worker_id"),
        currentJobId = row.stringOrNull("current_job_id"),
        channelCode = row.stringOrNull("channel_code"),
        lastErrorCode = row.stringOrNull("last_error_code"),
        lastErrorMessage = row.stringOrNull("last_error_message"),
    )
}
