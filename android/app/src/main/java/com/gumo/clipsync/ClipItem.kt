package com.gumo.clipsync

import androidx.room.*

@Entity(tableName = "clips")
data class ClipItem(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val text: String,
    val createdAt: Long = System.currentTimeMillis(),
    val isPinned: Boolean = false
)

@Dao
interface ClipDao {
    @Query("SELECT * FROM clips ORDER BY isPinned DESC, createdAt DESC")
    suspend fun getAll(): List<ClipItem>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(item: ClipItem): Long

    @Update
    suspend fun update(item: ClipItem)

    @Delete
    suspend fun delete(item: ClipItem)

    @Query("SELECT COUNT(*) FROM clips WHERE isPinned = 0")
    suspend fun countUnpinned(): Int

    @Query("DELETE FROM clips WHERE id = (SELECT id FROM clips WHERE isPinned = 0 ORDER BY createdAt ASC LIMIT 1)")
    suspend fun deleteOldestUnpinned()

    @Query("SELECT * FROM clips WHERE text = :text LIMIT 1")
    suspend fun findByText(text: String): ClipItem?
}

@Database(entities = [ClipItem::class], version = 1)
abstract class ClipDatabase : RoomDatabase() {
    abstract fun clipDao(): ClipDao

    companion object {
        @Volatile private var INSTANCE: ClipDatabase? = null
        fun get(context: android.content.Context): ClipDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(context, ClipDatabase::class.java, "clips.db")
                    .build().also { INSTANCE = it }
            }
    }
}
