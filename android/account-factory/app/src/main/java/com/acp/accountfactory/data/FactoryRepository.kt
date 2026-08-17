package com.acp.accountfactory.data

import com.acp.accountfactory.domain.AccountStage
import com.acp.accountfactory.domain.BatchGenerator
import com.acp.accountfactory.domain.Workflow
import kotlinx.coroutines.flow.Flow
import java.util.UUID

class FactoryRepository(private val dao: AccountDao) {
    fun latestBatch(): Flow<BatchEntity?> = dao.latestBatch()
    fun accounts(batchId: String): Flow<List<AccountEntity>> = dao.accounts(batchId)

    suspend fun createBatch(name: String = "Threads Affiliate", count: Int = 50, prefix: String = "acp") {
        val id = UUID.randomUUID().toString()
        val now = System.currentTimeMillis()
        dao.putBatch(BatchEntity(id, name, count, now))
        dao.putAccounts(BatchGenerator.generate(count, prefix).map { row ->
            AccountEntity(
                id = "$id:${row.sequence}", batchId = id, sequence = row.sequence,
                groupNo = row.groupNo, username = row.username,
                displayName = row.displayName, bio = row.bio, updatedAt = now,
            )
        })
    }

    suspend fun transition(id: String, to: AccountStage, error: String? = null) {
        val current = dao.account(id) ?: return
        require(Workflow.canTransition(current.stage, to)) { "Illegal transition ${current.stage} -> $to" }
        dao.putAccount(current.copy(stage = to, lastError = error, updatedAt = System.currentTimeMillis()))
    }

    suspend fun setConnecting(id: String, sessionId: String) {
        val current = dao.account(id) ?: return
        require(Workflow.canTransition(current.stage, AccountStage.ACP_CONNECTING))
        dao.putAccount(current.copy(
            stage = AccountStage.ACP_CONNECTING,
            oauthSessionId = sessionId,
            lastError = null,
            updatedAt = System.currentTimeMillis(),
        ))
    }

    suspend fun setActive(id: String, threadsUserId: String?, channelCode: String?) {
        val current = dao.account(id) ?: return
        require(Workflow.canTransition(current.stage, AccountStage.ACP_ACTIVE))
        dao.putAccount(current.copy(
            stage = AccountStage.ACP_ACTIVE,
            threadsUserId = threadsUserId,
            channelCode = channelCode,
            lastError = null,
            updatedAt = System.currentTimeMillis(),
        ))
    }

    suspend fun setOauthError(id: String, message: String) {
        val current = dao.account(id) ?: return
        if (current.stage == AccountStage.ACP_CONNECTING) {
            dao.putAccount(current.copy(stage = AccountStage.ERROR, lastError = message, updatedAt = System.currentTimeMillis()))
        }
    }
}
