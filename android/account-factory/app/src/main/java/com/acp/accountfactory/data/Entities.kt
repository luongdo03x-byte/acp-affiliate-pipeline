package com.acp.accountfactory.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import com.acp.accountfactory.domain.AccountStage

@Entity(tableName = "factory_batch")
data class BatchEntity(
    @PrimaryKey val id: String,
    val name: String,
    val targetCount: Int,
    val createdAt: Long,
)

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
