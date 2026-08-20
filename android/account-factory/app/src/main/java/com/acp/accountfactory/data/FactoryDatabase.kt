package com.acp.accountfactory.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters
import com.acp.accountfactory.domain.AccountStage

class Converters {
    @TypeConverter fun stageToString(stage: AccountStage) = stage.name
    @TypeConverter fun stringToStage(value: String) = AccountStage.valueOf(value)
}

@Database(entities = [BatchEntity::class, AccountEntity::class], version = 1, exportSchema = false)
@TypeConverters(Converters::class)
abstract class FactoryDatabase : RoomDatabase() {
    abstract fun dao(): AccountDao

    companion object {
        @Volatile private var instance: FactoryDatabase? = null
        fun get(context: Context): FactoryDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                FactoryDatabase::class.java,
                "acp-account-factory.db",
            ).build().also { instance = it }
        }
    }
}
