package app.smartdiary.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.smartdiary.DiaryApp
import app.smartdiary.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONArray
import org.json.JSONObject
import java.time.*

class DiaryViewModel(application: Application) : AndroidViewModel(application) {
    val repo = (application as DiaryApp).repo
    val entries = repo.entries().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val diaries = repo.objects("diary").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val memories = repo.objects("memory").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val people = repo.objects("person").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val messages = repo.objects("message").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val reminders = repo.objects("reminder").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val revisions = repo.objects("revision").stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val prefs = repo.objects("settings").map { it.firstOrNull() ?: defaultPrefs() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), defaultPrefs())
    val budget = repo.objects("budget").map { it.firstOrNull() }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)
    val busy = MutableStateFlow(false)
    val notice = MutableStateFlow("")
    val draft = MutableStateFlow("")
    val answer = MutableStateFlow<JSONObject?>(null)
    val timeline = MutableStateFlow<JSONObject?>(null)
    val selected = MutableStateFlow<Entry?>(null)
    init {
        viewModelScope.launch(Dispatchers.IO) { repo.dao.get(repo.owner.value, "draft", "text")?.let { draft.value = repo.decode(it).optString("text") } }
        viewModelScope.launch {
            entries.collect { current ->
                fun stale(refs: List<JSONObject>) = refs.any { ref ->
                    val entry=current.find { it.id==ref.optString("record_id") }
                    entry==null || entry.deleted || !entry.text.contains(ref.optString("quote")) ||
                        (!ref.optBoolean("local") && (entry.private || entry.dirty || entry.version!=ref.optInt("version")))
                }
                if (stale(answer.value?.optJSONArray("sources")?.objects() ?: emptyList())) answer.value=null
                val refs=(timeline.value?.optJSONArray("items")?.objects() ?: emptyList()).flatMap { it.optJSONArray("sources")?.objects() ?: emptyList() }
                if (stale(refs)) timeline.value=null
                selected.value?.let { open -> selected.value=current.find { it.id==open.id } }
            }
        }
    }
    fun task(block: suspend () -> Unit) {
        viewModelScope.launch {
            busy.value = true
            try { withContext(Dispatchers.IO) { block() } }
            catch (e: Exception) { notice.value = if (e is ApiException) e.message.orEmpty() else e.message ?: "暂时无法完成，记录仍保存在手机上" }
            finally { busy.value = false }
        }
    }
    fun updateDraft(text: String) {
        draft.value = text
        viewModelScope.launch(Dispatchers.IO) { if (draft.value==text) repo.put("draft", "text", JSONObject().put("text", text)) }
    }
    fun capture(text: String, private: Boolean, external: Boolean, occurred: String, callback: () -> Unit) {
        task {
            val link = Regex("https?://[^\\s]+").find(text)?.value.orEmpty()
            val entry = Entry(text=text.trim(), private=private, kind=if (link.isBlank()) "text" else "link", url=link,
                occurredAt=OffsetDateTime.parse(occurred).toInstant().toString(),
                sourceType=if (external) "external" else "personal")
            require(entry.text.isNotBlank()) { "说一句、写一笔，或者附上一张照片。" }
            repo.save(entry)
            repo.dao.remove(repo.owner.value, "draft", "text")
            withContext(Dispatchers.Main) { draft.value = ""; callback(); notice.value = "已保存到手机" }
        }
    }
    fun refresh() = task { repo.sync(); notice.value = "同步完成" }
    fun openSource(id: String) = task {
        selected.value = repo.entry(id) ?: Entry.from(repo.api.json("/v1/records/$id"))
    }
    fun generate(day: String) = task {
        val diary = repo.api.json("/v1/diaries/$day/generate", "POST")
        repo.put("diary", day, diary)
        notice.value = if (diary.optInt("version") == 0) "这一天还没有可整理的云端记录" else "日记已整理"
    }
    fun editParagraph(day: String, version: Int, id: String, text: String) = task {
        val diary = repo.api.json("/v1/diaries/$day", "PUT", JSONObject().put("base_version", version).put("paragraph_id", id).put("text", text))
        repo.put("diary", day, diary)
    }
    fun merge(day: String, version: Int) = task { repo.put("diary", day, repo.api.json("/v1/diaries/$day/merge?version=$version", "POST")) }
    fun find(query: String, useAI: Boolean, from: String = "", to: String = "", person: String = "", sourceType: String = "") = task {
        val zone = ZoneId.of(prefs.value.optString("timezone", "Asia/Shanghai"))
        val fromDate = from.takeIf { it.isNotBlank() }?.let(LocalDate::parse)
        val toDate = to.takeIf { it.isNotBlank() }?.let(LocalDate::parse)
        val local = entries.value.filter {
            val day=Instant.parse(it.occurredAt).atZone(zone).toLocalDate()
            query.isNotBlank() && it.text.contains(query, ignoreCase=true) &&
                (sourceType.isEmpty() || it.sourceType == sourceType) && (person.isEmpty() || it.text.contains(person)) &&
                (fromDate==null || !day.isBefore(fromDate)) && (toDate==null || !day.isAfter(toDate))
        }
        val payload = JSONObject().put("question", query)
        if (from.isNotBlank()) payload.put("day_from", from)
        if (to.isNotBlank()) payload.put("day_to", to)
        if (person.isNotBlank()) payload.put("person", person)
        if (sourceType.isNotBlank()) payload.put("source_type", sourceType)
        fun sources(records: List<Entry>) = records.map {
            JSONObject().put("record_id", it.id).put("quote", it.text).put("occurred_at", it.occurredAt).put("local", true)
                .put("version", it.version)
        }
        fun offlineAnswer() = JSONObject().put("answer", if (local.isEmpty()) "手机上没有找到这条线索，可以换个关键词。" else "找到手机上的原始记录。")
            .put("sources", JSONArray(sources(local)))
        if (repo.token.isEmpty()) {
            answer.value = offlineAnswer()
        } else {
            try {
                val result = if (useAI) repo.api.json("/v1/ask", "POST", payload) else {
                    val results = repo.api.json("/v1/search", "POST", payload).getJSONArray("results").objects()
                    JSONObject().put("answer", if (results.isEmpty()) "没有找到相关云端记录。" else "找到 ${results.size} 条相关记录。")
                        .put("sources", JSONArray(results.map { it.getJSONObject("source") }))
                }
                val cloud = result.optJSONArray("sources") ?: JSONArray()
                val stale=cloud.objects().any { ref ->
                    entries.value.find { it.id==ref.optString("record_id") }?.let { it.private || it.dirty || it.deleted || it.version!=ref.optInt("version") } != false
                }
                if (stale) { answer.value=offlineAnswer(); notice.value="部分记录正在更新，先显示手机里的原文" }
                else {
                    sources(local.filter { it.private || it.dirty }).forEach { cloud.put(it) }
                    answer.value=result.put("sources", cloud)
                }
            } catch (failure: Exception) {
                if (failure is ApiException && failure.code==422) throw failure
                answer.value=offlineAnswer()
                notice.value="云端暂时不可用，显示手机上的关键词搜索结果"
            }
        }
    }
    fun chat(text: String, recordId: String? = null) = task {
        val payload=JSONObject().put("question", text)
        if (recordId!=null) payload.put("record_id", recordId)
        val response = repo.api.json("/v1/chat", "POST", payload)
        repo.put("message", response.optString("id", java.util.UUID.randomUUID().toString()), response)
        repo.sync()
    }
    fun weekly() = task { answer.value = repo.api.json("/v1/reviews/weekly", "POST").let { it.put("answer", it.optString("text")) } }
    fun timeline(person: String = "", topic: String = "") = task {
        val p=java.net.URLEncoder.encode(person, "UTF-8")
        val t=java.net.URLEncoder.encode(topic, "UTF-8")
        timeline.value=repo.api.json("/v1/timelines?person=$p&topic=$t")
    }
    fun editMemory(memory: JSONObject, title: String, detail: String, people: String, topics: String) = task {
        fun split(text: String) = org.json.JSONArray(text.split(',', '，').map { it.trim() }.filter { it.isNotBlank() })
        repo.api.json("/v1/memories/${memory.getString("id")}", "PUT", JSONObject().put("title", title).put("detail", detail)
            .put("people", split(people)).put("topics", split(topics)))
        repo.clearDerived()
        repo.sync()
    }
    fun editPerson(person: JSONObject, name: String, aliases: String, mergeIds: List<String>) = task {
        val id=person.getString("id")
        repo.api.json("/v1/people/$id", "PUT", JSONObject().put("name", name)
            .put("aliases", org.json.JSONArray(aliases.split(',', '，').map { it.trim() }.filter { it.isNotBlank() })))
        if (mergeIds.isNotEmpty()) repo.api.json("/v1/people/$id/merge", "POST", JSONObject().put("source_ids", org.json.JSONArray(mergeIds)))
        repo.sync()
    }
    fun setPrefs(value: JSONObject) = task {
        ZoneId.of(value.getString("timezone"))
        for (key in listOf("diary_time", "quiet_start", "quiet_end")) {
            require(value.getString(key).length==5) { "时间格式应为 HH:mm" }
            LocalTime.parse(value.getString(key))
        }
        require(value.getInt("proactive_limit") in 0..10) { "提示次数应在 0—10 之间" }
        repo.put("settings", "preferences", value)
        repo.put("settings-outbox", "preferences", value)
        repo.requestSync()
        notice.value="设置已保存，联网后同步"
    }
    fun correct(entry: Entry, text: String, occurred: String) = task {
        repo.save(entry.copy(text=text, occurredAt=OffsetDateTime.parse(occurred).toInstant().toString(), dirty=true, status="local"))
        repo.clearDerived()
        selected.value = repo.entry(entry.id)
    }
    fun reminder(text: String, due: String) = task {
        val response = repo.api.json("/v1/reminders", "POST", JSONObject().put("text", text)
            .put("due_at", OffsetDateTime.parse(due).toInstant().toString()).put("confirmed", true))
        repo.put("reminder", response.getString("id"), response)
    }
    companion object {
        fun defaultPrefs() = JSONObject().put("timezone", "Asia/Shanghai").put("diary_time", "22:30")
            .put("proactivity", "balanced").put("proactive_limit", 1).put("quiet_start", "22:00").put("quiet_end", "08:00").put("notifications", true)
    }
}
