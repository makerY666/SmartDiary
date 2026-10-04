package app.smartdiary.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "vault", primaryKeys = ["owner", "kind", "id"])
data class VaultRow(val owner: String, val kind: String, val id: String, val stamp: Long, val encrypted: ByteArray)

@Dao
interface VaultDao {
    @Query("SELECT * FROM vault WHERE owner=:owner AND kind=:kind ORDER BY stamp DESC")
    fun observe(owner: String, kind: String): Flow<List<VaultRow>>
    @Query("SELECT * FROM vault WHERE owner=:owner AND kind=:kind ORDER BY stamp DESC")
    suspend fun list(owner: String, kind: String): List<VaultRow>
    @Query("SELECT * FROM vault WHERE owner=:owner AND kind=:kind AND id=:id LIMIT 1")
    suspend fun get(owner: String, kind: String, id: String): VaultRow?
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(row: VaultRow)
    @Query("DELETE FROM vault WHERE owner=:owner AND kind=:kind AND id=:id")
    suspend fun remove(owner: String, kind: String, id: String)
    @Query("UPDATE vault SET owner=:newOwner WHERE owner=:oldOwner")
    suspend fun adopt(oldOwner: String, newOwner: String)
}

@Database(entities = [VaultRow::class], version = 1, exportSchema = true)
abstract class LocalDatabase : RoomDatabase() { abstract fun vault(): VaultDao }
