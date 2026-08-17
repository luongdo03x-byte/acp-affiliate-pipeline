package com.acp.accountfactory.network

import org.json.JSONObject

data class DiscoveryDto(
    val service: String,
    val apiVersion: Int,
)

data class EnrollmentDto(
    val deviceToken: String,
)

object ControllerDiscovery {
    fun parseDiscovery(body: String): DiscoveryDto? = runCatching {
        val json = JSONObject(body)
        val service = json.optString("service")
        val apiVersion = json.optInt("api_version", -1)
        if (service != "account-factory" || apiVersion != 2) return null
        DiscoveryDto(service = service, apiVersion = apiVersion)
    }.getOrNull()

    fun parseEnrollment(body: String): EnrollmentDto? = runCatching {
        val json = JSONObject(body)
        if (json.optString("service") != "account-factory") return null
        if (json.optInt("api_version", -1) != 2) return null
        val token = json.optString("device_token").trim()
        if (token.isBlank()) return null
        EnrollmentDto(deviceToken = token)
    }.getOrNull()

    fun private24Candidates(ipv4: String, port: Int): List<String> {
        if (port !in 1..65535) return emptyList()
        val parts = ipv4.split('.')
        if (parts.size != 4) return emptyList()
        val octets = parts.map { it.toIntOrNull() ?: return emptyList() }
        if (octets.any { it !in 0..255 }) return emptyList()
        if (!isPrivateIpv4(octets)) return emptyList()

        val ownHost = octets[3]
        val prefix = "${octets[0]}.${octets[1]}.${octets[2]}"
        return (1..254)
            .asSequence()
            .filter { it != ownHost }
            .map { "http://$prefix.$it:$port" }
            .toList()
    }

    private fun isPrivateIpv4(parts: List<Int>): Boolean {
        val first = parts[0]
        val second = parts[1]
        return first == 10 ||
            (first == 172 && second in 16..31) ||
            (first == 192 && second == 168) ||
            (first == 169 && second == 254)
    }
}
