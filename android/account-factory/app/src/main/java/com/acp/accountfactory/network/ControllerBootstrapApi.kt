package com.acp.accountfactory.network

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

internal fun cloudPairingRequest(baseUrl: String, code: String, deviceId: String, deviceName: String): Request {
    val url = baseUrl.trim().toHttpUrlOrNull()
        ?: throw IllegalArgumentException("Địa chỉ máy chủ không hợp lệ")
    require(url.isHttps && url.username.isEmpty() && url.password.isEmpty()
        && url.encodedPath == "/" && url.query == null && url.fragment == null) {
        "Nhập địa chỉ HTTPS của máy chủ, không kèm đường dẫn"
    }
    val body = JSONObject().put("code", code.trim())
        .put("device_id", deviceId).put("device_name", deviceName)
        .toString().toRequestBody("application/json".toMediaType())
    return Request.Builder()
        .url(url.newBuilder().addPathSegments("api/factory/pair").build())
        .post(body).build()
}

class ControllerBootstrapApi(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(350, TimeUnit.MILLISECONDS)
        .readTimeout(500, TimeUnit.MILLISECONDS)
        .callTimeout(700, TimeUnit.MILLISECONDS)
        .build(),
) {
    private val jsonType = "application/json".toMediaType()
    private val cloudClient = client.newBuilder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .callTimeout(20, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    suspend fun pair(baseUrl: String, code: String, deviceId: String, deviceName: String): EnrollmentDto =
        withContext(Dispatchers.IO) {
            val request = cloudPairingRequest(baseUrl, code, deviceId, deviceName)
            cloudClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    throw IllegalStateException("Ghép đôi thất bại (${response.code}). Kiểm tra địa chỉ và mã còn hạn.")
                }
                ControllerDiscovery.parseEnrollment(response.body.string())
                    ?: throw IllegalStateException("Phản hồi ghép đôi không hợp lệ")
            }
        }

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

    suspend fun validateCredential(baseUrl: String, credential: String): Boolean =
        withContext(Dispatchers.IO) {
            val clean = credential.trim()
            if (clean.isBlank()) return@withContext false
            val request = Request.Builder()
                .url(baseUrl.trimEnd('/') + "/api/factory/v2/dashboard")
                // The controller auth bridge recognizes an enrolled device
                // credential in this legacy slot, preserving the existing API client.
                .header("X-ACP-Factory-Key", clean)
                .get()
                .build()
            runCatching {
                client.newCall(request).execute().use { response -> response.isSuccessful }
            }.getOrDefault(false)
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
