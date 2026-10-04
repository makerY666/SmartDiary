package app.smartdiary.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.json.JSONArray
import java.io.InputStream
import java.io.OutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

suspend fun Repository.exportLocal(output: OutputStream) = withContext(Dispatchers.IO) {
    require(!app.smartdiary.recording.RecordingService.state.value.active) { "请先结束并保存录音，再导出完整备份" }
    val kinds = listOf("record", "diary", "memory", "person", "message", "reminder", "settings", "tombstone", "revision", "draft")
    val manifest = JSONObject().put("format", "smartdiary-local-v1").put("owner", owner.value)
    for (kind in kinds) manifest.put(kind, JSONArray(dao.list(owner.value, kind).map { row -> decode(row).put("_vault_id", row.id) }
        .filter { kind!="record" || !it.optBoolean("deleted") }))
    ZipOutputStream(output).use { zip ->
        zip.putNextEntry(ZipEntry("manifest.json")); zip.write(manifest.toString(2).toByteArray(Charsets.UTF_8)); zip.closeEntry()
        val records = manifest.getJSONArray("record").objects().map(Entry::from)
        for (media in records.flatMap { it.media }.distinctBy { it.id }) {
            if (!mediaFile(media.id).exists()) {
                require(token.isNotEmpty()) { "部分附件尚未下载，连接同步后再导出完整备份" }
                storeMedia(media.id, api.bytes("/v1/attachments/${media.id}"))
            }
            zip.putNextEntry(ZipEntry("attachments/${media.id}")); zip.write(readMedia(media.id)); zip.closeEntry()
        }
        for (diary in manifest.getJSONArray("diary").objects()) {
            val day = diary.getString("day")
            zip.putNextEntry(ZipEntry("diaries/$day.md"))
            zip.write(("# $day\n\n" + (diary.optJSONArray("paragraphs") ?: JSONArray()).objects().joinToString("\n\n") { it.optString("text") }).toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
    }
}

suspend fun Repository.restoreLocal(input: InputStream): Int = withContext(Dispatchers.IO) {
    val files = mutableMapOf<String, ByteArray>()
    var total = 0
    ZipInputStream(input).use { zip ->
        while (true) {
            val entry = zip.nextEntry ?: break
            require(files.size < 5000) { "导出包条目过多" }
            val output = java.io.ByteArrayOutputStream()
            val buffer = ByteArray(8192)
            while (true) {
                val count = zip.read(buffer)
                if (count < 0) break
                total += count
                require(total <= 100 * 1024 * 1024 && output.size() + count <= 20 * 1024 * 1024) { "导出包超过恢复上限" }
                output.write(buffer, 0, count)
            }
            files[entry.name] = output.toByteArray()
            zip.closeEntry()
        }
    }
    val manifest = JSONObject(String(files["manifest.json"] ?: error("缺少清单"), Charsets.UTF_8))
    require(manifest.optString("format") == "smartdiary-local-v1") { "请选择手机导出的拾记备份" }
    require(manifest.optString("owner") in listOf(owner.value, "guest")) { "请登录导出此备份的账户" }
    val imported = (manifest.optJSONArray("record") ?: JSONArray()).objects().map(Entry::from)
    imported.forEach { entry ->
        java.util.UUID.fromString(entry.id)
        if (!entry.deleted) entry.media.forEach { media -> java.util.UUID.fromString(media.id); require(files.containsKey("attachments/${media.id}") || mediaFile(media.id).exists()) { "备份缺少原始附件" } }
    }
    for (item in (manifest.optJSONArray("tombstone") ?: JSONArray()).objects()) {
        val id=item.getString("id")
        java.util.UUID.fromString(id)
        this@restoreLocal.entry(id)?.let { delete(it) }
        put("tombstone", id, item)
    }
    var restored = 0
    for (entry in imported) {
        java.util.UUID.fromString(entry.id)
        if (entry.deleted || dao.get(owner.value, "tombstone", entry.id) != null || this@restoreLocal.entry(entry.id) != null) continue
        for (media in entry.media) files["attachments/${media.id}"]?.let { java.util.UUID.fromString(media.id); storeMedia(media.id, it) }
        save(entry.copy(dirty=!entry.private, version=if(entry.private) entry.version else 0, conflict=null,
            media=entry.media.map { it.copy(uploaded=false, preserveText=it.preserveText ||
                (entry.text.isNotBlank() && (entry.version>1 || entry.status in listOf("ready", "ready_basic")))) }), false)
        restored++
    }
    for (kind in listOf("memory", "message", "revision", "person", "reminder", "settings", "draft")) {
        for (item in (manifest.optJSONArray(kind) ?: JSONArray()).objects()) {
            val refs = (item.optJSONArray("sources") ?: JSONArray()).objects().toMutableList()
            item.optJSONObject("source")?.let { refs.add(it) }
            if (refs.any { ref -> this@restoreLocal.entry(ref.optString("record_id"))?.let { it.deleted || !it.text.contains(ref.optString("quote")) } != false }) continue
            if (kind=="revision" && this@restoreLocal.entry(item.optString("record_id"))==null) continue
            val id=if (kind=="settings") "preferences" else if(kind=="draft") "text" else item.optString("_vault_id", item.optString("id", java.util.UUID.randomUUID().toString()))
            if (dao.get(owner.value, kind, id)==null) put(kind, id, item)
            if (kind=="settings") put("settings-outbox", "preferences", item)
        }
    }
    for (item in (manifest.optJSONArray("diary") ?: JSONArray()).objects()) {
        val refs = ((item.optJSONArray("paragraphs") ?: JSONArray()).objects() + (item.optJSONArray("timeline") ?: JSONArray()).objects())
            .flatMap { (it.optJSONArray("sources") ?: JSONArray()).objects() }
        if (refs.all { this@restoreLocal.entry(it.optString("record_id"))?.deleted == false }) put("diary", item.getString("day"), item)
    }
    requestSync()
    restored
}
