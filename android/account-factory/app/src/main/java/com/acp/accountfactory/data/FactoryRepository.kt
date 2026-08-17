package com.acp.accountfactory.data

import kotlinx.coroutines.flow.Flow

/**
 * Optional local cache for controller snapshots.
 *
 * This repository deliberately contains no workflow transition methods. Factory V2
 * REST responses are authoritative; Room may only mirror display data for later
 * offline/cache work.
 */
class FactoryRepository(private val dao: AccountDao) {
    fun latestBatch(): Flow<BatchEntity?> = dao.latestBatch()
    fun accounts(batchId: String): Flow<List<AccountEntity>> = dao.accounts(batchId)

    suspend fun cacheSnapshot(batch: BatchEntity?, accounts: List<AccountEntity>) {
        if (batch != null) dao.putBatch(batch)
        if (accounts.isNotEmpty()) dao.putAccounts(accounts)
    }
}
