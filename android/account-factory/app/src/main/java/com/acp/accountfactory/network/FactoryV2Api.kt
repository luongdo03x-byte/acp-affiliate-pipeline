package com.acp.accountfactory.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class FactoryV2Api(private val client: OkHttpClient = OkHttpClient()) {
    private val jsonType = "application/json".toMediaType()

    private fun endpoint(baseUrl: String, path: String): String = baseUrl.trimEnd('/') + path

    private fun requestBuilder(baseUrl: String, factoryKey: String, path: String): Request.Builder =
        Request.Builder()
            .url(endpoint(baseUrl, path))
            .header("X-ACP-Factory-Key", factoryKey)

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

    private suspend fun get(baseUrl: String, factoryKey: String, path: String): String =
        withContext(Dispatchers.IO) {
            val request = requestBuilder(baseUrl, factoryKey, path).get().build()
            client.newCall(request).execute().use { response ->
                val body = response.body.string()
                if (!response.isSuccessful) throw safeError(response.code, body)
                body
            }
        }

    private suspend fun post(
        baseUrl: String,
        factoryKey: String,
        path: String,
        bodyJson: String = "{}",
    ): String = withContext(Dispatchers.IO) {
        val request = requestBuilder(baseUrl, factoryKey, path)
            .post(bodyJson.toRequestBody(jsonType))
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body.string()
            if (!response.isSuccessful) throw safeError(response.code, body)
            body
        }
    }

    suspend fun dashboard(baseUrl: String, factoryKey: String): DashboardDto =
        FactoryV2Json.parseDashboard(get(baseUrl, factoryKey, "/api/factory/v2/dashboard"))

    suspend fun accounts(baseUrl: String, factoryKey: String): List<FactoryAccountDto> =
        FactoryV2Json.parseAccounts(get(baseUrl, factoryKey, "/api/factory/v2/accounts"))

    suspend fun workers(baseUrl: String, factoryKey: String): List<FactoryWorkerDto> =
        FactoryV2Json.parseWorkers(get(baseUrl, factoryKey, "/api/factory/v2/workers"))

    suspend fun checkpoints(baseUrl: String, factoryKey: String): List<FactoryCheckpointDto> =
        FactoryV2Json.parseCheckpoints(get(baseUrl, factoryKey, "/api/factory/v2/checkpoints"))

    suspend fun continueCheckpoint(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/checkpoints/$id/continue"))

    suspend fun retryCheckpoint(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/checkpoints/$id/retry"))

    suspend fun snoozeCheckpoint(
        baseUrl: String,
        factoryKey: String,
        id: String,
        minutes: Int,
    ): CommandAcceptedDto {
        val body = JSONObject().put("minutes", minutes).toString()
        return FactoryV2Json.parseCommand(
            post(baseUrl, factoryKey, "/api/factory/v2/checkpoints/$id/snooze", body)
        )
    }

    suspend fun pauseBatch(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/batches/$id/pause"))

    suspend fun resumeBatch(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/batches/$id/resume"))

    suspend fun stopAccount(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/accounts/$id/stop"))

    suspend fun retryAccount(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/accounts/$id/retry"))

    suspend fun drainWorker(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/workers/$id/drain"))

    suspend fun restartWorker(baseUrl: String, factoryKey: String, id: String): CommandAcceptedDto =
        FactoryV2Json.parseCommand(post(baseUrl, factoryKey, "/api/factory/v2/workers/$id/restart"))
}
