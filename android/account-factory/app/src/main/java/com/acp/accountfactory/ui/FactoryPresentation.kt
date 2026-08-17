package com.acp.accountfactory.ui

import java.time.Duration
import java.time.Instant

enum class AccountAction { NONE, RETRY, CONNECT_OAUTH }

fun capacityLabel(state: String?): String = when (state?.uppercase()) {
    "GREEN" -> "Ổn định"
    "YELLOW" -> "Theo dõi"
    "RED" -> "Giảm tải"
    "EMERGENCY" -> "Khẩn cấp"
    else -> "Không rõ"
}

fun checkpointActionsEnabled(status: String): Boolean =
    status.uppercase() in setOf("OPEN", "SNOOZED")

fun primaryAccountAction(stage: String, errorCode: String?): AccountAction = when {
    stage == "THREADS_CREATED" -> AccountAction.CONNECT_OAUTH
    stage == "RETRY_PENDING" && errorCode == "OAUTH_FAILED" -> AccountAction.CONNECT_OAUTH
    stage in setOf("ERROR", "RETRY_PENDING", "NEEDS_CONFIRMATION") -> AccountAction.RETRY
    else -> AccountAction.NONE
}

fun waitingDuration(createdAt: String, now: Instant = Instant.now()): String {
    val created = runCatching { Instant.parse(createdAt) }.getOrNull() ?: return "—"
    val minutes = Duration.between(created, now).toMinutes().coerceAtLeast(0)
    val hours = minutes / 60
    val rest = minutes % 60
    return when {
        hours <= 0 -> "$rest phút"
        rest == 0L -> "$hours giờ"
        else -> "$hours giờ $rest phút"
    }
}
