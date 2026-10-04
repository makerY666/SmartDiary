package app.smartdiary.data

import android.content.Context
import android.net.Uri
import android.util.Base64
import androidx.room.Room
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.concurrent.TimeUnit

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class Repository(val context: Context) {
    val crypto = Crypto()
    private val db = Room.databaseBuilder(context, LocalDatabase::class.java, "diary-vault.db").build()
    val dao = db.vault()
    private val prefs = context.getSharedPreferences("preferences", Context.MODE_PRIVATE)
    val owner = MutableStateFlow(prefs.getString("owner", "guest")!!)
    val api = Api(this)
    private val syncLock = Mutex()
    private val writeLock = Mutex()
    var serverUrl: String
        get() = prefs.getString("server", "http://10.0.2.2:8000")!!
        set(value) { prefs.edit().putString("server", value.trim()).apply() }
    var token: String
        get() = secret("token")
        set(value) { setSecret("token", value) }
    var appLock: Boolean
        get() = prefs.getBoolean("app-lock", false)
        set(value) { prefs.edit().putBoolean("app-lock", value).apply() }
    private fun secret(key: String): String = prefs.getString(key, null)?.let {
        String(crypto.decrypt(Base64.decode(it, Base64.NO_WRAP)), Charsets.UTF_8)
    } ?: ""
    private fun setSecret(key: String, value: String) {
        prefs.edit().putString(key, Base64.encodeToString(crypto.encrypt(value.toByteArray()), Base64.NO_WRAP)).apply()
    }
    fun decode(row: VaultRow): JSONObject = JSONObject(String(crypto.decrypt(row.encrypted), Charsets.UTF_8))
    suspend fun put(kind: String, id: String, data: JSONObject, stamp: Long = System.currentTimeMillis()) {
        dao.put(VaultRow(owner.value, kind, id, stamp, crypto.encrypt(data.toString().toByteArray(Charsets.UTF_8))))
    }
    fun entries(): Flow<List<Entry>> = owner.flatMapLatest { user -> dao.observe(user, "record") }
        .map { rows -> rows.map { Entry.from(decode(it)) }.filterNot { it.deleted } }
    fun objects(kind: String): Flow<List<JSONObject>> = owner.flatMapLatest { user -> dao.observe(user, kind) }
        .map { rows -> rows.map(::decode) }
    suspend fun entry(id: String): Entry? = dao.get(owner.value, "record", id)?.let { Entry.from(decode(it)) }
    suspend fun save(entry: Entry, enqueue: Boolean = true) = withContext(Dispatchers.IO) {
        writeLock.withLock {
            require(entry.deleted || dao.get(owner.value, "tombstone", entry.id)==null) { "记录已删除，不能重新写入" }
            val previous = this@Repository.entry(entry.id)
            if (previous != null && !entry.deleted && (previous.text != entry.text || previous.occurredAt != entry.occurredAt || previous.sourceType != entry.sourceType)) {
                put("revision", java.util.UUID.randomUUID().toString(), JSONObject().put("record_id", entry.id)
                    .put("version", previous.version).put("snapshot", previous.json()).put("saved_at", Instant.now().toString()))
            }
            put("record", entry.id, entry.json(), Instant.parse(entry.recordedAt).toEpochMilli())
        }
        if (enqueue) requestSync()
    }
    fun mediaFile(id: String) = File(context.filesDir, "media/$id.enc").apply { parentFile?.mkdirs() }
    @Synchronized
    fun storeMedia(id: String, data: ByteArray) {
        val file = mediaFile(id)
        val temporary = File(file.parentFile, "${file.name}.tmp")
        java.io.FileOutputStream(temporary).use { out -> out.write(crypto.encrypt(data)); out.flush(); out.fd.sync() }
        check(temporary.renameTo(file)) { "附件保存未完成" }
    }
    fun readMedia(id: String): ByteArray = crypto.decrypt(mediaFile(id).readBytes())
    suspend fun appendMedia(id: String, media: Media): Entry = writeLock.withLock {
        val latest=entry(id) ?: error("记录不存在")
        require(!latest.deleted) { "记录已删除" }
        val next=if(latest.media.any { it.id==media.id }) latest else latest.copy(media=latest.media + media)
        put("record", id, next.json(), Instant.parse(next.recordedAt).toEpochMilli())
        requestSync()
        next
    }
    suspend fun attach(uri: Uri, current: Entry? = null): Entry = withContext(Dispatchers.IO) {
        val mime = context.contentResolver.getType(uri) ?: "image/jpeg"
        val data = context.contentResolver.openInputStream(uri)!!.use { input ->
            val out = java.io.ByteArrayOutputStream()
            val chunk = ByteArray(8192)
            while (out.size() <= 20 * 1024 * 1024) { val n = input.read(chunk); if (n < 0) break; out.write(chunk, 0, n) }
            val bytes = out.toByteArray()
            require(bytes.size <= 20 * 1024 * 1024) { "图片超过 20MB" }
            bytes
        }
        val media = Media(java.util.UUID.randomUUID().toString(), mime)
        storeMedia(media.id, data)
        val entry = current ?: Entry(kind="image").also { save(it) }
        appendMedia(entry.id, media)
    }
    suspend fun login(username: String, password: String, invitation: String, register: Boolean) = withContext(Dispatchers.IO) {
        val response = api.json("/v1/auth/" + if (register) "register" else "login", "POST",
            JSONObject().put("username", username).put("password", password).put("invitation", invitation))
        val user = response.getString("user_id")
        val old = owner.value
        require(old == "guest" || old == user) { "此安装已绑定另一个账户。请在独立安装中使用，避免混合日记。" }
        if (old == "guest") dao.adopt("guest", user)
        prefs.edit().putString("owner", user).apply()
        owner.value = user
        token = response.getString("token")
        requestSync()
    }
    suspend fun delete(entry: Entry) {
        val latest=this@Repository.entry(entry.id) ?: entry
        if (app.smartdiary.recording.RecordingService.state.value.recordId==entry.id && app.smartdiary.recording.RecordingService.state.value.active)
            context.startService(android.content.Intent(context, app.smartdiary.recording.RecordingService::class.java).setAction("stop"))
        put("tombstone", entry.id, JSONObject().put("id", entry.id))
        save(latest.copy(deleted=true, dirty=true, deleteCloud=latest.version > 0, text="", originalText="", url="", conflict=null, error="", media=emptyList()))
        for (media in latest.media) mediaFile(media.id).delete()
        File(context.filesDir, "journals").listFiles()?.filter { it.name.startsWith("${entry.id}_") }?.forEach { it.delete() }
        for (row in dao.list(owner.value, "revision")) if (decode(row).optString("record_id")==entry.id) dao.remove(owner.value, "revision", row.id)
        clearDerived()
    }
    suspend fun makePrivate(entry: Entry) {
        val latest=this@Repository.entry(entry.id) ?: entry
        save(latest.copy(private=true, dirty=false, deleteCloud=latest.version > 0, status="local", conflict=null))
        clearDerived()
    }
    suspend fun clearDerived() {
        for (kind in listOf("diary", "memory", "message", "person")) {
            for (row in dao.list(owner.value, kind)) dao.remove(owner.value, kind, row.id)
        }
    }
    fun requestSync() {
        if (token.isEmpty()) return
        val request = OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }
    fun periodicSync() {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("sync-periodic", ExistingPeriodicWorkPolicy.KEEP, request)
    }
    suspend fun sync() = withContext(Dispatchers.IO) {
        syncLock.withLock {
            if (token.isEmpty()) return@withLock
            val user = owner.value
            dao.get(user, "settings-outbox", "preferences")?.let { pending ->
                val data = decode(pending)
                api.json("/v1/settings", "PUT", data)
                val latest = dao.get(user, "settings-outbox", "preferences")
                if (latest?.encrypted?.contentEquals(pending.encrypted) == true) dao.remove(user, "settings-outbox", "preferences")
            }
            for (row in dao.list(user, "record").sortedBy { it.stamp }) {
                var entry = Entry.from(decode(row))
                if (entry.deleteCloud) {
                    try { api.json("/v1/records/${entry.id}", "DELETE") }
                    catch (e: ApiException) { if (e.code != 404 && e.code != 410) throw e }
                    entry = writeLock.withLock {
                        val latest=this@Repository.entry(entry.id) ?: entry
                        latest.copy(deleteCloud=false, dirty=false).also { put("record", it.id, it.json(), Instant.parse(it.recordedAt).toEpochMilli()) }
                    }
                }
                if (entry.deleted) { dao.remove(user, "record", entry.id); continue }
                if (entry.private || entry.conflict != null) continue
                if (entry.dirty) {
                    try {
                        val server = Entry.from(api.json("/v1/records", "POST", entry.wire()))
                        entry = writeLock.withLock {
                            val latest = entry(entry.id) ?: entry
                            val edited = latest.text != entry.text || latest.occurredAt != entry.occurredAt || latest.sourceType != entry.sourceType
                            val next = server.copy(text=if (edited) latest.text else server.text,
                                occurredAt=if (edited) latest.occurredAt else server.occurredAt,
                                sourceType=latest.sourceType, dirty=edited, media=latest.media,
                                private=latest.private, deleted=latest.deleted,
                                deleteCloud=latest.private || latest.deleted)
                            put("record", next.id, next.json(), Instant.parse(next.recordedAt).toEpochMilli())
                            next
                        }
                    } catch (e: ApiException) {
                        if (e.code == 409 || e.code == 410) {
                            writeLock.withLock {
                                val latest=this@Repository.entry(entry.id) ?: entry
                                if (!latest.private && !latest.deleted) {
                                    val next=if(e.code==409) latest.copy(conflict=e.body.optJSONObject("detail")?.optJSONObject("server"), status="conflict")
                                        else latest.copy(private=true, dirty=false, status="local", error="云端已删除，保留手机上的副本")
                                    put("record", next.id, next.json(), Instant.parse(next.recordedAt).toEpochMilli())
                                }
                            }
                            continue
                        }
                        throw e
                    }
                }
                if (entry.private || entry.deleted) { requestSync(); continue }
                val media = entry.media.mapIndexed { position, item ->
                    if (!item.uploaded && mediaFile(item.id).exists()) {
                        api.upload("/v1/records/${entry.id}/attachments/${item.id}", readMedia(item.id), item.mime, item.id, item.duration, position, item.preserveText)
                        item.copy(uploaded=true)
                    } else item
                }
                writeLock.withLock {
                    val latest = entry(entry.id) ?: entry
                    val updated = latest.media.map { item -> media.find { it.id==item.id } ?: item }
                    put("record", latest.id, latest.copy(media=updated).json(), Instant.parse(latest.recordedAt).toEpochMilli())
                }
            }
            var cursor = prefs.getLong("cursor-$user", 0)
            do {
                val response = api.json("/v1/sync?cursor=$cursor")
                for (change in response.getJSONArray("changes").objects()) {
                    val kind = change.getString("kind")
                    val data = change.getJSONObject("payload")
                    if (kind == "record") {
                        val remote = Entry.from(data)
                        writeLock.withLock {
                        val current = entry(remote.id)
                        if (current?.private == true || current?.dirty == true || current?.conflict != null) return@withLock
                        if (remote.deleted) {
                            put("tombstone", remote.id, JSONObject().put("id", remote.id))
                            dao.remove(user, "record", remote.id)
                            current?.media?.forEach { mediaFile(it.id).delete() }
                            clearDerived()
                        } else {
                            val remoteIds=remote.media.map { it.id }.toSet()
                            val media = remote.media.map { item -> current?.media?.find { it.id == item.id } ?: item.copy(uploaded=true) } +
                                (current?.media ?: emptyList()).filter { !it.uploaded && it.id !in remoteIds }
                            put("record", remote.id, remote.copy(media=media, dirty=false).json(), Instant.parse(remote.recordedAt).toEpochMilli())
                        }
                        }
                    } else if (kind == "diary") {
                        val day = data.getString("day")
                        val cached = dao.get(user, "diary", day)?.let(::decode)
                        if (cached == null || cached.optInt("version") <= data.optInt("version")) put("diary", day, data)
                    } else if (kind == "message") put("message", data.getString("id"), data)
                    else if (kind == "settings" && dao.get(user, "settings-outbox", "preferences")==null) put("settings", "preferences", data)
                }
                cursor = response.getLong("cursor")
                prefs.edit().putLong("cursor-$user", cursor).commit()
            } while (response.getBoolean("has_more"))
            for ((path, kind, key) in listOf(Triple("/v1/memories", "memory", "id"), Triple("/v1/people", "person", "id"), Triple("/v1/reminders", "reminder", "id"), Triple("/v1/messages", "message", "id"))) {
                val objects = api.array(path).objects()
                val ids = objects.map { it.getString(key) }.toSet()
                for (row in dao.list(user, kind)) if (row.id !in ids) dao.remove(user, kind, row.id)
                for (item in objects) put(kind, item.getString(key), item)
            }
            if (dao.get(user, "settings-outbox", "preferences")==null) put("settings", "preferences", api.json("/v1/settings"))
            put("budget", "current", api.json("/v1/budget"))
        }
    }
    suspend fun resolve(entry: Entry, keepLocal: Boolean) {
        val server = entry.conflict ?: return
        if (keepLocal) save(entry.copy(version=server.getInt("version"), conflict=null, dirty=true))
        else save(Entry.from(server).copy(media=entry.media, dirty=false))
    }
    suspend fun mediaBytes(media: Media): ByteArray = withContext(Dispatchers.IO) {
        if (!mediaFile(media.id).exists()) storeMedia(media.id, api.bytes("/v1/attachments/${media.id}"))
        readMedia(media.id)
    }
}
