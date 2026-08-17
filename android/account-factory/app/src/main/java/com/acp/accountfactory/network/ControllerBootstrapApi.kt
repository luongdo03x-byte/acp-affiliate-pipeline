package com.acp.accountfactory.network

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class ControllerBootstrapApi(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(350, TimeUnit.MILLISECONDS)
        .readTimeout(500, TimeUnit.MILLISECONDS)
        .callTimeout(700, TimeUnit.MILLISECONDS)
        .build(),
) {
    private val jsonType = "application/json".toMediaType()

    suspend fun discover(baseUrl: String): DiscoveryDto? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + "/api/factory/discovery")
            .get()
            .build()
        runCatching {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@use null
                ControllerDiscovery.parseDiscovery(response.body.string())
            }
        }.getOrNull()
    }

    suspend fun enroll(baseUrl: String, deviceId: String, deviceName: String): EnrollmentDto =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("device_id", deviceId)
                .put("device_name", deviceName)
                .toString()
                .toRequestBody(jsonType)
            val request = Request.Builder()
                .url(baseUrl.trimEnd('/') + "/api/factory/enroll")
                .post(body)
                .build()
            client.newCall(request).execute().use { response ->
                val responseBody = response.body.string()
                if (!response.isSuccessful) {
                    val safe = runCatching {
                        JSONObject(responseBody).optString("error").trim().take(200)
                    }.getOrNull().orEmpty()
                    val suffix = safe.takeIf { it.isNotBlank() }?.let { ": $it" }.orEmpty()
                    throw IllegalStateException("Controller enrollment failed (${response.code})$suffix")
                }
                return@use ControllerDiscovery.parseEnrollment(responseBody)
                    ?: throw IllegalStateException("Controller enrollment response không hợp lệ")
            }
        }
}
