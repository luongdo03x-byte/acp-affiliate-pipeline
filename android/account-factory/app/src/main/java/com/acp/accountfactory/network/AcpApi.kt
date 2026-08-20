package com.acp.accountfactory.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class AcpApi(private val client: OkHttpClient = OkHttpClient()) {
    data class Started(val sessionId: String, val authorizationUrl: String, val status: String)
    data class Status(
        val status: String,
        val actualUsername: String?,
        val threadsUserId: String?,
        val channelCode: String?,
        val error: String?,
    )

    private fun endpoint(baseUrl: String, path: String) = baseUrl.trimEnd('/') + path

    suspend fun start(
        baseUrl: String,
        factoryKey: String,
        expectedUsername: String,
        batchId: String,
        accountLocalId: String,
    ): Started = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("expected_username", expectedUsername)
            .put("batch_id", batchId)
            .put("account_local_id", accountLocalId)
            .toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder()
            .url(endpoint(baseUrl, "/oauth/account-factory/start"))
            .header("X-ACP-Factory-Key", factoryKey)
            .post(body)
            .build()
        client.newCall(req).execute().use { res ->
            val text = res.body.string()
            if (!res.isSuccessful) throw IllegalStateException("ACP start failed (${res.code})")
            val json = JSONObject(text)
            Started(json.getString("session_id"), json.getString("authorization_url"), json.getString("status"))
        }
    }

    suspend fun status(baseUrl: String, factoryKey: String, sessionId: String): Status = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url(endpoint(baseUrl, "/oauth/account-factory/session/$sessionId"))
            .header("X-ACP-Factory-Key", factoryKey)
            .get()
            .build()
        client.newCall(req).execute().use { res ->
            val text = res.body.string()
            if (!res.isSuccessful) throw IllegalStateException("ACP status failed (${res.code})")
            val json = JSONObject(text)
            Status(
                status = json.getString("status"),
                actualUsername = json.optString("actual_username").takeIf { it.isNotBlank() && it != "null" },
                threadsUserId = json.optString("threads_user_id").takeIf { it.isNotBlank() && it != "null" },
                channelCode = json.optString("channel_code").takeIf { it.isNotBlank() && it != "null" },
                error = json.optString("error").takeIf { it.isNotBlank() && it != "null" },
            )
        }
    }
}
