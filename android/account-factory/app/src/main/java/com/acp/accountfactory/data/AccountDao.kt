package com.acp.accountfactory.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface AccountDao {
    @Query("SELECT * FROM factory_batch ORDER BY createdAt DESC LIMIT 1")
    fun latestBatch(): Flow<BatchEntity?>

    @Query("SELECT * FROM factory_account WHERE batchId=:batchId ORDER BY sequence")
    fun accounts(batchId: String): Flow<List<AccountEntity>>

    @Query("SELECT * FROM factory_account WHERE id=:id LIMIT 1")
    suspend fun account(id: String): AccountEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putBatch(batch: BatchEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putAccounts(accounts: List<AccountEntity>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putAccount(account: AccountEntity)
}
