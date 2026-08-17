package com.acp.accountfactory.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.acp.accountfactory.runner.LocalRunnerIdentityStore
import com.acp.accountfactory.settings.FactorySettingsStore
import java.net.Inet4Address
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope

enum class BootstrapState {
    ALREADY_CONFIGURED,
    ENROLLED,
    NO_PRIVATE_WIFI,
    CONTROLLER_NOT_FOUND,
    ENROLLMENT_FAILED,
}

data class BootstrapResult(
    val state: BootstrapState,
    val controllerUrl: String? = null,
    val message: String? = null,
)

class ZeroConfigBootstrap(
    context: Context,
    private val settings: FactorySettingsStore,
    private val identityStore: LocalRunnerIdentityStore,
    private val api: ControllerBootstrapApi = ControllerBootstrapApi(),
) {
    private val appContext = context.applicationContext

    suspend fun ensureConfigured(): BootstrapResult {
        if (settings.isConfigured()) {
            return BootstrapResult(
                state = BootstrapState.ALREADY_CONFIGURED,
                controllerUrl = settings.baseUrl,
            )
        }

        val ipv4 = currentPrivateWifiIpv4()
            ?: return BootstrapResult(BootstrapState.NO_PRIVATE_WIFI)
        val candidates = ControllerDiscovery.private24Candidates(ipv4, DEFAULT_CONTROLLER_PORT)
        val controller = discoverFirst(candidates)
            ?: return BootstrapResult(BootstrapState.CONTROLLER_NOT_FOUND)

        val identity = identityStore.getOrCreate()
        return runCatching {
            val enrolled = api.enroll(
                baseUrl = controller,
                deviceId = identity.deviceId,
                deviceName = identity.deviceName,
            )
            settings.saveEnrollment(controller, enrolled.deviceToken)
            BootstrapResult(
                state = BootstrapState.ENROLLED,
                controllerUrl = controller,
            )
        }.getOrElse { error ->
            BootstrapResult(
                state = BootstrapState.ENROLLMENT_FAILED,
                controllerUrl = controller,
                message = error.message?.take(240),
            )
        }
    }

    private suspend fun discoverFirst(candidates: List<String>): String? {
        for (chunk in candidates.chunked(DISCOVERY_CONCURRENCY)) {
            val found = coroutineScope {
                chunk.map { candidate ->
                    async { candidate.takeIf { api.discover(candidate) != null } }
                }.awaitAll().firstOrNull { it != null }
            }
            if (found != null) return found
        }
        return null
    }

    private fun currentPrivateWifiIpv4(): String? {
        val manager = appContext.getSystemService(ConnectivityManager::class.java) ?: return null
        val network = manager.activeNetwork ?: return null
        val capabilities = manager.getNetworkCapabilities(network) ?: return null
        if (!capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return null
        val properties = manager.getLinkProperties(network) ?: return null
        return properties.linkAddresses
            .map { it.address }
            .filterIsInstance<Inet4Address>()
            .map { it.hostAddress.orEmpty() }
            .firstOrNull {
                ControllerDiscovery.private24Candidates(it, DEFAULT_CONTROLLER_PORT).isNotEmpty()
            }
    }

    private companion object {
        const val DEFAULT_CONTROLLER_PORT = 5001
        const val DISCOVERY_CONCURRENCY = 24
    }
}
