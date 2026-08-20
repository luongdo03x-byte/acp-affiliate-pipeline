package com.acp.accountfactory.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

data class FactoryConnection(val baseUrl: String, val factoryKey: String) {
    init {
        require(baseUrl.isNotBlank()) { "ACP Base URL is required" }
        require(factoryKey.isNotBlank()) { "Factory Key is required" }
    }
}

interface FactoryV2ApiClient {
    suspend fun dashboard(connection: FactoryConnection): DashboardDto
    suspend fun accounts(connection: FactoryConnection): List<FactoryAccountDto>
    suspend fun workers(connection: FactoryConnection): List<FactoryWorkerDto>
    suspend fun checkpoints(connection: FactoryConnection): List<FactoryCheckpointDto>
    suspend fun continueCheckpoint(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun retryCheckpoint(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun snoozeCheckpoint(connection: FactoryConnection, id: String, minutes: Int): CommandAcceptedDto
    suspend fun pauseBatch(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun resumeBatch(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun stopAccount(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun retryAccount(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun drainWorker(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun restartWorker(connection: FactoryConnection, id: String): CommandAcceptedDto
    suspend fun startOAuth(connection: FactoryConnection, id: String): StartedOAuthDto
    suspend fun oauthStatus(connection: FactoryConnection, id: String): FactoryAccountDto

    suspend fun registerLocalRunner(
        connection: FactoryConnection,
        deviceId: String,
        deviceName: String,
    ): FactoryRunnerDto = error("registerLocalRunner not implemented")

    suspend fun heartbeatRunner(
        connection: FactoryConnection,
        workerId: String,
        currentAccountId: String?,
        currentJobId: String?,
    ): FactoryRunnerDto = error("heartbeatRunner not implemented")

    suspend fun nextRunnerCommand(
        connection: FactoryConnection,
        workerId: String,
    ): RunnerCommandDto? = error("nextRunnerCommand not implemented")

    suspend fun submitRunnerCommandResult(
        connection: FactoryConnection,
        workerId: String,
        commandId: String,
        status: String,
        result: Map<String, Any?>,
    ): CommandAcceptedDto = error("submitRunnerCommandResult not implemented")

    suspend fun runners(connection: FactoryConnection): List<FactoryRunnerDto> =
        error("runners not implemented")

    suspend fun createAccount(
        connection: FactoryConnection,
        executionTarget: String,
        batchName: String = "Phone/AVD Pilot",
    ): FactoryAccountDto = error("createAccount not implemented")
}

class FactoryV2Api(private val client: OkHttpClient = OkHttpClient()) : FactoryV2ApiClient {
    private val jsonType = "application/json".toMediaType()

    private fun endpoint(baseUrl: String, path: String): String = baseUrl.trimEnd('/') + path

    private fun requestBuilder(connection: FactoryConnection, path: String): Request.Builder =
        Request.Builder()
            .url(endpoint(connection.baseUrl, path))
            .header("X-ACP-Factory-Key", connection.factoryKey)

    private fun safeError(code: Int, body: String): IllegalStateException {
        val allowlisted = runCatching {
            JSONObject(body).optString("error")
                .takeIf { it.isNotBlank() && it != "null" }
                ?.replace(Regex("\\s+"), " ")
                ?.take(240)
        }.getOrNull()
        val suffix = allowlisted?.let { ": $it" } ?: ""
        return IllegalStateException("ACP request failed ($code)$suffix")
    }

    private suspend fun get(connection: FactoryConnection, path: String): String =
        withContext(Dispatchers.IO) {
            val request = requestBuilder(connection, path).get().build()
            client.newCall(request).execute().use { response ->
                val body = response.body.string()
                if (!response.isSuccessful) throw safeError(response.code, body)
                body
            }
        }

    private suspend fun post(
        connection: FactoryConnection,
        path: String,
        bodyJson: String = "{}",
    ): String = withContext(Dispatchers.IO) {
        val request = requestBuilder(connection, path)
            .post(bodyJson.toRequestBody(jsonType))
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body.string()
            if (!response.isSuccessful) throw safeError(response.code, body)
            body
        }
    }

    override suspend fun dashboard(connection: FactoryConnection): DashboardDto =
        FactoryV2Json.parseDashboard(get(connection, "/api/factory/v2/dashboard"))

    override suspend fun accounts(connection: FactoryConnection): List<FactoryAccountDto> =
        FactoryV2Json.parseAccounts(get(connection, "/api/factory/v2/accounts"))

    override suspend fun workers(connection: FactoryConnection): List<FactoryWorkerDto> =
        FactoryV2Json.parseWorkers(get(connection, "/api/factory/v2/workers"))

    override suspend fun checkpoints(connection: FactoryConnection): List<FactoryCheckpointDto> =
        FactoryV2Json.parseCheckpoints(get(connection, "/api/factory/v2/checkpoints"))

    override suspend fun continueCheckpoint(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/checkpoints/$id/continue"))

    override suspend fun retryCheckpoint(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/checkpoints/$id/retry"))

    override suspend fun snoozeCheckpoint(
        connection: FactoryConnection,
        id: String,
        minutes: Int,
    ): CommandAcceptedDto {
        val body = JSONObject().put("minutes", minutes).toString()
        return FactoryV2Json.parseCommand(
            post(connection, "/api/factory/v2/checkpoints/$id/snooze", body)
        )
    }

    override suspend fun pauseBatch(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/batches/$id/pause"))

    override suspend fun resumeBatch(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/batches/$id/resume"))

    override suspend fun stopAccount(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/accounts/$id/stop"))

    override suspend fun retryAccount(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/accounts/$id/retry"))

    override suspend fun drainWorker(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/workers/$id/drain"))

    override suspend fun restartWorker(connection: FactoryConnection, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(connection, "/api/factory/v2/workers/$id/restart"))

    override suspend fun startOAuth(connection: FactoryConnection, id: String): StartedOAuthDto =
        FactoryV2Json.parseStartedOAuth(post(connection, "/api/factory/v2/accounts/$id/oauth/start"))

    override suspend fun oauthStatus(connection: FactoryConnection, id: String): FactoryAccountDto =
        FactoryV2Json.parseAccountResponse(get(connection, "/api/factory/v2/accounts/$id/oauth/status"))

    override suspend fun registerLocalRunner(
        connection: FactoryConnection,
        deviceId: String,
        deviceName: String,
    ): FactoryRunnerDto {
        val body = JSONObject()
            .put("device_id", deviceId)
            .put("device_name", deviceName)
            .toString()
        return FactoryV2Json.parseRunnerResponse(
            post(connection, "/api/factory/v2/runners/local/register", body)
        )
    }

    override suspend fun heartbeatRunner(
        connection: FactoryConnection,
        workerId: String,
        currentAccountId: String?,
        currentJobId: String?,
    ): FactoryRunnerDto {
        val body = JSONObject()
            .put("current_account_id", currentAccountId ?: JSONObject.NULL)
            .put("current_job_id", currentJobId ?: JSONObject.NULL)
            .toString()
        return FactoryV2Json.parseRunnerResponse(
            post(connection, "/api/factory/v2/runners/$workerId/heartbeat", body)
        )
    }

    override suspend fun nextRunnerCommand(
        connection: FactoryConnection,
        workerId: String,
    ): RunnerCommandDto? = FactoryV2Json.parseRunnerCommandResponse(
        get(connection, "/api/factory/v2/runners/$workerId/commands/next")
    )

    override suspend fun submitRunnerCommandResult(
        connection: FactoryConnection,
        workerId: String,
        commandId: String,
        status: String,
        result: Map<String, Any?>,
    ): CommandAcceptedDto {
        val resultJson = JSONObject()
        result.forEach { (key, value) -> resultJson.put(key, value ?: JSONObject.NULL) }
        val body = JSONObject()
            .put("status", status)
            .put("result", resultJson)
            .toString()
        return FactoryV2Json.parseCommand(
            post(
                connection,
                "/api/factory/v2/runners/$workerId/commands/$commandId/result",
                body,
            )
        )
    }

    override suspend fun runners(connection: FactoryConnection): List<FactoryRunnerDto> =
        FactoryV2Json.parseRunners(get(connection, "/api/factory/v2/runners"))

    override suspend fun createAccount(
        connection: FactoryConnection,
        executionTarget: String,
        batchName: String,
    ): FactoryAccountDto {
        val body = JSONObject()
            .put("execution_target", executionTarget)
            .put("batch_name", batchName)
            .toString()
        return FactoryV2Json.parseAccountResponse(
            post(connection, "/api/factory/v2/accounts", body)
        )
    }
}
