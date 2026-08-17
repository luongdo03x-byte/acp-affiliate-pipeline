package com.acp.accountfactory.ui

import java.time.Duration
import java.time.Instant

fun capacityLabel(state: String?): String = when (state?.uppercase()) {
    "GREEN" -> "Ổn định"
    "YELLOW" -> "Theo dõi"
    "RED" -> "Giảm tải"
    "EMERGENCY" -> "Khẩn cấp"
    else -> "Không rõ"
}

fun checkpointActionsEnabled(status: String): Boolean =
    status.uppercase() in setOf("OPEN", "SNOOZED")

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
