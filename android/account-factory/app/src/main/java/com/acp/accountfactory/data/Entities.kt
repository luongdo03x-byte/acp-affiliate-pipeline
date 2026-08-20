package com.acp.accountfactory.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import com.acp.accountfactory.domain.AccountStage

/**
 * Legacy Room snapshot retained for optional offline display/cache work.
 * Factory V2 REST responses are authoritative; this row must never drive
 * controller transitions or worker commands.
 */
@Entity(tableName = "factory_batch")
data class BatchEntity(
    @PrimaryKey val id: String,
    val name: String,
    val targetCount: Int,
    val createdAt: Long,
)

/**
 * Cached display snapshot only. `stage` may mirror a previously fetched value,
 * but UI/controller actions must always use FactoryV2Api + FactoryViewModel.
 * No credential, OTP/CAPTCHA, provider token, app secret, or master key belongs
 * in this entity.
 */
@Entity(tableName = "factory_account")
data class AccountEntity(
    @PrimaryKey val id: String,
    val batchId: String,
    val sequence: Int,
    val groupNo: Int,
    val username: String,
    val displayName: String,
    val bio: String,
    val stage: AccountStage = AccountStage.PLANNED,
    val oauthSessionId: String? = null,
    val threadsUserId: String? = null,
    val channelCode: String? = null,
    val lastError: String? = null,
    val updatedAt: Long = System.currentTimeMillis(),
)
