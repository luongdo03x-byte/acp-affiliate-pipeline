package com.acp.accountfactory.runner

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import com.acp.accountfactory.MainActivity
import com.acp.accountfactory.network.FactoryV2Api
import com.acp.accountfactory.network.ZeroConfigBootstrap
import com.acp.accountfactory.settings.FactorySettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class LocalRunnerService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var bootstrapJob: Job? = null
    private var runner: LocalDeviceRunner? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification("Đang kết nối Factory Controller…"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        bootstrapJob?.cancel()
        runner?.stop()
        runner = null

        bootstrapJob = serviceScope.launch {
            val settings = FactorySettingsStore(this@LocalRunnerService)
            val identityStore = LocalRunnerIdentityStore(this@LocalRunnerService)
            val bootstrap = ZeroConfigBootstrap(
                context = this@LocalRunnerService,
                settings = settings,
                identityStore = identityStore,
            ).ensureConfigured()

            if (!bootstrap.ready || !settings.isConfigured()) {
                stopSelf(startId)
                return@launch
            }

            getSystemService(NotificationManager::class.java).notify(
                NOTIFICATION_ID,
                buildNotification("Local device runner đang hoạt động"),
            )
            runner = LocalDeviceRunner(
                api = FactoryV2Api(),
                connectionProvider = { settings.connection() },
                identityStore = identityStore,
                actions = LocalDeviceActions(this@LocalRunnerService),
            ).also { it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        bootstrapJob?.cancel()
        bootstrapJob = null
        runner?.stop()
        runner = null
        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Account Factory runner",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Giữ kết nối runner điện thoại với Factory Controller"
            }
        )
    }

    private fun buildNotification(message: String): Notification {
        val launchIntent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            launchIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("Account Factory")
            .setContentText(message)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "account_factory_runner"
        private const val NOTIFICATION_ID = 4102

        fun start(context: Context) {
            context.startForegroundService(Intent(context, LocalRunnerService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LocalRunnerService::class.java))
        }
    }
}
