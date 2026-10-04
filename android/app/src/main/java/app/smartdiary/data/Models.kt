package app.smartdiary.data

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).map { optString(it) }

data class Media(val id: String, val mime: String, val duration: Double = 0.0, val uploaded: Boolean = false, val preserveText: Boolean = false) {
    fun json() = JSONObject().put("id", id).put("mime_type", mime).put("duration_seconds", duration).put("uploaded", uploaded).put("preserve_text", preserveText)
    companion object { fun from(j: JSONObject) = Media(j.getString("id"), j.getString("mime_type"), j.optDouble("duration_seconds", 0.0), j.optBoolean("uploaded"), j.optBoolean("preserve_text")) }
}

data class Entry(
    val id: String = UUID.randomUUID().toString(), val kind: String = "text", val text: String = "",
    val originalText: String = text, val url: String = "", val occurredAt: String = Instant.now().toString(),
    val recordedAt: String = Instant.now().toString(), val version: Int = 0, val private: Boolean = false,
    val dirty: Boolean = true, val deleted: Boolean = false, val deleteCloud: Boolean = false,
    val status: String = "local", val error: String = "", val sourceType: String = "personal",
    val media: List<Media> = emptyList(), val conflict: JSONObject? = null, val supersededBy: String = "",
    val parentRecordId: String = "", val relation: String = "followup"
) {
    fun json() = JSONObject().put("id", id).put("kind", kind).put("text", text).put("original_text", originalText)
        .put("url", url).put("occurred_at", occurredAt).put("recorded_at", recordedAt).put("version", version)
        .put("private", private).put("dirty", dirty).put("deleted", deleted).put("delete_cloud", deleteCloud)
        .put("status", status).put("error", error).put("source_type", sourceType).put("superseded_by", supersededBy)
        .put("attachments", JSONArray(media.map { it.json() })).put("conflict", conflict)
        .put("parent_record_id", parentRecordId.ifBlank { null }).put("relation", relation)
    fun wire() = JSONObject().put("id", id).put("kind", kind).put("text", text).put("url", url)
        .put("occurred_at", occurredAt).put("recorded_at", recordedAt).put("base_version", version).put("source_type", sourceType)
        .put("parent_record_id", parentRecordId.ifBlank { null }).put("relation", relation)
    companion object {
        fun from(j: JSONObject) = Entry(id=j.getString("id"), kind=j.optString("kind", "text"), text=j.optString("text"),
            originalText=j.optString("original_text", j.optString("text")), url=j.optString("url"),
            occurredAt=j.getString("occurred_at"), recordedAt=j.getString("recorded_at"), version=j.optInt("version"),
            private=j.optBoolean("private"), dirty=j.optBoolean("dirty"), deleted=j.optBoolean("deleted"),
            deleteCloud=j.optBoolean("delete_cloud"), status=j.optString("status", "local"), error=j.optString("error"),
            sourceType=j.optString("source_type", "personal"), media=(j.optJSONArray("attachments") ?: JSONArray()).objects().map(Media::from),
            conflict=j.optJSONObject("conflict"), supersededBy=j.optString("superseded_by"),
            parentRecordId=if(j.isNull("parent_record_id")) "" else j.optString("parent_record_id"), relation=j.optString("relation", "followup"))
    }
}
