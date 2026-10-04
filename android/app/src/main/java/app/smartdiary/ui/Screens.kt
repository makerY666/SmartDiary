@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
package app.smartdiary.ui

import android.content.Intent
import android.media.MediaPlayer
import android.speech.tts.TextToSpeech
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.smartdiary.data.*
import app.smartdiary.recording.RecordingService
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.time.*
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
fun DiaryRoot(vm: DiaryViewModel, unlocked: MutableStateFlow<Boolean>, action: MutableStateFlow<String>,
              record: () -> Unit, unlock: () -> Unit) {
    val ready by unlocked.collectAsState()
    val launch by action.collectAsState()
    val notice by vm.notice.collectAsState()
    val busy by vm.busy.collectAsState()
    val selected by vm.selected.collectAsState()
    var tab by remember { mutableIntStateOf(0) }
    var capture by remember { mutableStateOf(false) }
    var share by remember { mutableStateOf(false) }
    val snack = remember { SnackbarHostState() }
    val preferences by vm.prefs.collectAsState()
    val diaryZone = runCatching { ZoneId.of(preferences.optString("timezone", "Asia/Shanghai")) }.getOrDefault(ZoneId.of("Asia/Shanghai"))
    LaunchedEffect(notice) { if (notice.isNotEmpty()) { snack.showSnackbar(notice); vm.notice.value = "" } }
    LaunchedEffect(launch, ready) {
        if (ready) {
            when (launch) {
                "app.smartdiary.RECORD" -> { tab=0; record() }
                "app.smartdiary.WRITE" -> { tab=0; capture=true }
                "app.smartdiary.SHARE" -> { tab=0; capture=true; share=true }
                "app.smartdiary.INBOX" -> tab=4
            }
            action.value = ""
        }
    }
    CompositionLocalProvider(LocalDiaryZone provides diaryZone) { DiaryTheme {
        if (!ready) {
            Box(Modifier.fillMaxSize().background(Paper), contentAlignment=Alignment.Center) {
                Column(horizontalAlignment=Alignment.CenterHorizontally, verticalArrangement=Arrangement.spacedBy(20.dp)) {
                    Icon(Icons.Outlined.Lock, "记录已锁定", Modifier.size(48.dp), tint=Pine)
                    Text("你的每一页，都在这里。", style=MaterialTheme.typography.titleLarge)
                    Button(onClick=unlock) { Text("解锁拾记") }
                }
            }
        } else Scaffold(containerColor=Paper, snackbarHost={ SnackbarHost(snack) },
            bottomBar={ NavigationBar(containerColor=Paper) {
                val tabs = listOf("此刻" to Icons.Outlined.EditNote, "日记" to Icons.Outlined.MenuBook,
                    "记忆" to Icons.Outlined.ManageSearch, "我的" to Icons.Outlined.Tune)
                tabs.forEachIndexed { index, pair -> NavigationBarItem(selected=tab==index, onClick={ tab=index },
                    icon={ Icon(pair.second, pair.first) }, label={ Text(pair.first) }) }
            } }, topBar={ TopAppBar(title={ Text("拾记", style=MaterialTheme.typography.titleLarge) },
                colors=TopAppBarDefaults.topAppBarColors(containerColor=Paper), actions={
                    IconButton(onClick={ tab=4 }) { Icon(Icons.Outlined.Forum, "与记忆聊聊") }
                    IconButton(onClick={ vm.refresh() }, enabled=!busy) { Icon(Icons.Outlined.Sync, "同步") }
                }) }) { padding ->
            Column(Modifier.padding(padding).fillMaxSize()) {
                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth(), color=Pine)
                Box(Modifier.fillMaxSize(), contentAlignment=Alignment.TopCenter) {
                    Box(Modifier.widthIn(max=720.dp).fillMaxSize()) {
                        when (tab) {
                            0 -> TodayScreen(vm, record) { capture=true; share=false }
                            1 -> DiaryScreen(vm)
                            2 -> MemoryScreen(vm)
                            3 -> SettingsScreen(vm)
                            4 -> CompanionScreen(vm)
                        }
                    }
                }
            }
        }
        if (capture && ready) CaptureSheet(vm, share) { capture=false }
        if (selected != null && ready) EntrySheet(vm, selected!!) { vm.selected.value=null }
    } }
}

@Composable
private fun Empty(title: String, body: String, icon: androidx.compose.ui.graphics.vector.ImageVector) {
    Column(Modifier.fillMaxWidth().padding(vertical=42.dp, horizontal=12.dp),
        horizontalAlignment=Alignment.CenterHorizontally, verticalArrangement=Arrangement.spacedBy(12.dp)) {
        Icon(icon, null, Modifier.size(40.dp), tint=Pine)
        Text(title, style=MaterialTheme.typography.titleLarge)
        Text(body, color=Muted, style=MaterialTheme.typography.bodyMedium)
    }
}

private fun shortDate(value: String, pattern: String = "MM月dd日 HH:mm", zone: ZoneId = ZoneId.of("Asia/Shanghai")): String = runCatching {
    Instant.parse(value).atZone(zone).format(DateTimeFormatter.ofPattern(pattern))
}.getOrDefault(value)

@Composable
private fun Sources(vm: DiaryViewModel, sources: JSONArray) {
    val zone = LocalDiaryZone.current
    for (source in sources.objects()) {
        Surface(onClick={ vm.openSource(source.getString("record_id")) }, color=Color(0xFFECEEE5),
            shape=RoundedCornerShape(8.dp), modifier=Modifier.fillMaxWidth().padding(top=8.dp)) {
            Column(Modifier.padding(12.dp), verticalArrangement=Arrangement.spacedBy(4.dp)) {
                Text((if (source.optBoolean("local")) "仅本地 · " else "来源 · ") + shortDate(source.optString("occurred_at"), zone=zone),
                    color=Pine, style=MaterialTheme.typography.labelLarge)
                Text(source.optString("quote"), maxLines=3, overflow=TextOverflow.Ellipsis, style=MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
fun TodayScreen(vm: DiaryViewModel, record: () -> Unit, write: () -> Unit) {
    val zone = LocalDiaryZone.current
    val entries by vm.entries.collectAsState()
    val recording by RecordingService.state.collectAsState()
    val context = LocalContext.current
    val photos = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) vm.task { vm.repo.attach(uri); vm.notice.value = "图片已保存到手机" }
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding=PaddingValues(horizontal=22.dp, vertical=12.dp),
        verticalArrangement=Arrangement.spacedBy(14.dp)) {
        item {
            Text(LocalDate.now(zone).format(DateTimeFormatter.ofPattern("M月d日 · EEEE", Locale.CHINA)), color=Muted)
            Spacer(Modifier.height(10.dp))
            Text("把这一刻留下。", style=MaterialTheme.typography.headlineLarge)
            Spacer(Modifier.height(8.dp))
            Text("一句话，一张照片。剩下的慢慢整理。", color=Muted)
        }
        item {
            if (recording.active) Surface(color=Pine, shape=RoundedCornerShape(14.dp)) {
                Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement=Arrangement.spacedBy(14.dp)) {
                    Text(if (recording.paused) "已暂停" else "正在听你说", color=Color.White, style=MaterialTheme.typography.titleLarge)
                    Text("${recording.seconds / 60}:${(recording.seconds % 60).toString().padStart(2, '0')} · 每段自动保存", color=Color.White)
                    Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                        FilledTonalButton(onClick={ context.startService(Intent(context, RecordingService::class.java).setAction(if (recording.paused) "resume" else "pause")) }) {
                            Text(if (recording.paused) "继续" else "暂停")
                        }
                        FilledTonalButton(onClick={ context.startService(Intent(context, RecordingService::class.java).setAction("stop")) }) { Text("结束并保存") }
                    }
                }
            } else Column(verticalArrangement=Arrangement.spacedBy(10.dp)) {
                Button(onClick=record, modifier=Modifier.fillMaxWidth().height(60.dp), shape=RoundedCornerShape(12.dp)) {
                    Icon(Icons.Outlined.Mic, null); Spacer(Modifier.width(10.dp)); Text("说一句", fontSize=18.sp)
                }
                Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                    OutlinedButton(onClick=write, modifier=Modifier.weight(1f).height(50.dp), shape=RoundedCornerShape(10.dp)) { Icon(Icons.Outlined.Edit, null); Spacer(Modifier.width(8.dp)); Text("写一笔") }
                    OutlinedButton(onClick={ photos.launch("image/*") }, modifier=Modifier.weight(1f).height(50.dp), shape=RoundedCornerShape(10.dp)) { Icon(Icons.Outlined.AddPhotoAlternate, null); Spacer(Modifier.width(8.dp)); Text("留张照片") }
                }
            }
        }
        if (recording.error.isNotBlank()) item { Text(recording.error, color=Rust) }
        item { HorizontalDivider(color=Color(0xFFDADFD4)); Spacer(Modifier.height(16.dp)); Text("留下的片刻", style=MaterialTheme.typography.titleLarge) }
        if (entries.isEmpty()) item { Empty("从一句话开始", "不用想好标题，也不用分类。", Icons.Outlined.BookmarkBorder) }
        items(entries, key={ it.id }) { entry ->
            Surface(onClick={ vm.selected.value=entry }, color=Color.White.copy(alpha=.65f), shape=RoundedCornerShape(12.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement=Arrangement.SpaceBetween) {
                        Text(shortDate(entry.occurredAt, zone=zone), style=MaterialTheme.typography.labelLarge, color=Muted)
                        Text(if (entry.private) "仅本地" else when {
                            entry.conflict != null -> "待合并"
                            entry.dirty || entry.version == 0 || entry.media.any { !it.uploaded } -> "已保存 · 待同步"
                            entry.status == "ready" -> "已整理"
                            entry.status == "ready_basic" -> "原文已整理"
                            entry.status == "failed" -> "处理失败"
                            entry.status == "budget_blocked" -> "云端额度不足"
                            entry.status == "needs_ai" -> "附件已保存 · 待处理"
                            else -> "已同步 · 处理中"
                        }, style=MaterialTheme.typography.labelLarge, color=if (entry.status in listOf("failed", "budget_blocked")) Rust else Pine)
                    }
                    Text(entry.text.ifBlank { if (entry.kind=="audio") "一段语音 · ${entry.media.sumOf { it.duration }.toInt()} 秒" else "一张照片" },
                        style=MaterialTheme.typography.bodyLarge, maxLines=4, overflow=TextOverflow.Ellipsis)
                    if (entry.sourceType=="external") Text("外部知识", color=Rust, style=MaterialTheme.typography.labelLarge)
                    if (entry.supersededBy.isNotEmpty()) Text("已有修正版，回查以修正版为准", color=Muted, style=MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}

@Composable
fun CaptureSheet(vm: DiaryViewModel, share: Boolean, close: () -> Unit) {
    val zone = LocalDiaryZone.current
    val draft by vm.draft.collectAsState()
    val busy by vm.busy.collectAsState()
    var private by remember { mutableStateOf(false) }
    var external by remember { mutableStateOf(share) }
    var occurred by remember { mutableStateOf(OffsetDateTime.now(zone).toString()) }
    var dateEdit by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest=close, containerColor=Paper) {
        Column(Modifier.padding(22.dp).imePadding().verticalScroll(rememberScrollState()), verticalArrangement=Arrangement.spacedBy(14.dp)) {
            Text("留下一笔", style=MaterialTheme.typography.titleLarge)
            OutlinedTextField(draft, vm::updateDraft, placeholder={ Text("发生了什么？想到什么？直接写就好。") },
                minLines=5, modifier=Modifier.fillMaxWidth())
            Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(private, { private=it }); Text("仅保存在手机上") }
            Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(external, { external=it }); Text("这是摘录或外部知识") }
            TextButton(onClick={ dateEdit=!dateEdit }) { Icon(Icons.Outlined.Schedule, null); Spacer(Modifier.width(8.dp)); Text("发生时间 · ${shortDate(runCatching { OffsetDateTime.parse(occurred).toInstant().toString() }.getOrDefault(occurred), zone=zone)}") }
            if (dateEdit) DateTimeField(occurred, { occurred=it }, "发生时间", Modifier.fillMaxWidth())
            Button(onClick={ vm.capture(draft, private, external, occurred, close) }, enabled=!busy && draft.isNotBlank(), modifier=Modifier.fillMaxWidth().height(52.dp)) { Text("保存到手机") }
            Text("草稿自动保存，关闭后可以继续写。", color=Muted, style=MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
fun DiaryScreen(vm: DiaryViewModel) {
    val zone = LocalDiaryZone.current
    val diaries by vm.diaries.collectAsState()
    val entries by vm.entries.collectAsState()
    var day by remember { mutableStateOf(LocalDate.now(zone)) }
    var timeline by remember { mutableStateOf(false) }
    var edit by remember { mutableStateOf<JSONObject?>(null) }
    val diary = diaries.find { it.optString("day")==day.toString() }
    val hasDiary=(diary?.optJSONArray("paragraphs")?.length() ?: 0)>0
    val local = entries.filter { shortDate(it.occurredAt, "yyyy-MM-dd", zone)==day.toString() &&
        (it.private || it.dirty || it.version==0 || !hasDiary || it.status in listOf("failed", "budget_blocked", "needs_ai")) }
        .sortedBy { it.occurredAt }
    LazyColumn(Modifier.fillMaxSize(), contentPadding=PaddingValues(22.dp), verticalArrangement=Arrangement.spacedBy(18.dp)) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment=Alignment.CenterVertically, horizontalArrangement=Arrangement.SpaceBetween) {
                IconButton(onClick={ day=day.minusDays(1) }) { Icon(Icons.Outlined.ChevronLeft, "前一天") }
                Text(day.format(DateTimeFormatter.ofPattern("yyyy年 M月d日")), style=MaterialTheme.typography.titleLarge)
                IconButton(onClick={ day=day.plusDays(1) }) { Icon(Icons.Outlined.ChevronRight, "后一天") }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                FilterChip(selected=!timeline, onClick={ timeline=false }, label={ Text("日记阅读") })
                FilterChip(selected=timeline, onClick={ timeline=true }, label={ Text("结构化回顾") })
                Spacer(Modifier.weight(1f)); IconButton(onClick={ vm.generate(day.toString()) }) { Icon(Icons.Outlined.AutoStories, "现在整理") }
            }
        }
        if (diary == null || (diary.optJSONArray("paragraphs")?.length() ?: 0)==0) item { Empty("这一页还在等待", "每天晚上自动整理，也可以点上方按钮。", Icons.Outlined.MenuBook) }
        val list = diary?.optJSONArray(if (timeline) "timeline" else "paragraphs")?.objects() ?: emptyList()
        items(list) { item ->
            Column(verticalArrangement=Arrangement.spacedBy(10.dp)) {
                if (timeline) Text(item.optString("time") + "  " + item.optString("title"), color=Pine, fontWeight=FontWeight.Medium)
                if (item.optString("kind") in listOf("external", "knowledge")) Text("摘录与知识", color=Rust, style=MaterialTheme.typography.labelLarge)
                Text(item.optString("text"), fontSize=if (timeline) 17.sp else 19.sp, lineHeight=if (timeline) 29.sp else 34.sp,
                    fontFamily=if (timeline) FontFamily.Default else FontFamily.Serif)
                if (item.optBoolean("stale")) Text("原始依据已修正，请复核这一段。你的手工内容已保留。", color=Rust)
                Sources(vm, item.optJSONArray("sources") ?: JSONArray())
                if (!timeline) TextButton(onClick={ edit=item }) { Text(if (item.optBoolean("edited")) "已手工修改 · 再编辑" else "修改这一段") }
                HorizontalDivider(color=Color(0xFFDFE3D9))
            }
        }
        if ((diary?.optJSONArray("pending")?.length() ?: 0)>0) item {
            Column(verticalArrangement=Arrangement.spacedBy(10.dp)) {
                Text("有新的补充", style=MaterialTheme.typography.titleLarge)
                Text("你修改过的段落已保留。以下内容确认后加入。", color=Muted)
                diary!!.getJSONArray("pending").objects().forEach { Text(it.optString("text")) }
                OutlinedButton(onClick={ vm.merge(day.toString(), diary.getInt("version")) }) { Text("合并补充") }
            }
        }
        if (local.isNotEmpty()) item { Text("手机里的原始片段", style=MaterialTheme.typography.titleLarge) }
        items(local) { item -> Column {
            Text(if(item.private) "仅本地" else if(item.dirty || item.version==0 || item.media.any { !it.uploaded }) "已保存 · 待同步" else "原始记录 · 可继续整理", color=Muted)
            Text(item.text.ifBlank { "原始附件已保存" }); TextButton(onClick={ vm.selected.value=item }) { Text("查看原始记录") }
        } }
    }
    if (edit != null) {
        var text by remember(edit) { mutableStateOf(edit!!.optString("text")) }
        AlertDialog(onDismissRequest={ edit=null }, title={ Text("修改这一段") }, text={ OutlinedTextField(text, { text=it }, minLines=4) },
            confirmButton={ TextButton(onClick={ vm.editParagraph(day.toString(), diary!!.getInt("version"), edit!!.getString("id"), text); edit=null }) { Text("保存修改") } },
            dismissButton={ TextButton(onClick={ edit=null }) { Text("取消") } })
    }
}

@Composable
fun MemoryScreen(vm: DiaryViewModel) {
    val answer by vm.answer.collectAsState()
    val people by vm.people.collectAsState()
    val memories by vm.memories.collectAsState()
    val timeline by vm.timeline.collectAsState()
    var memoryEdit by remember { mutableStateOf<JSONObject?>(null) }
    var personEdit by remember { mutableStateOf<JSONObject?>(null) }
    var query by remember { mutableStateOf("") }
    var ai by remember { mutableStateOf(true) }
    var filters by remember { mutableStateOf(false) }
    var from by remember { mutableStateOf("") }
    var to by remember { mutableStateOf("") }
    var person by remember { mutableStateOf("") }
    var external by remember { mutableStateOf(false) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding=PaddingValues(22.dp), verticalArrangement=Arrangement.spacedBy(16.dp)) {
        item {
            Text("从一个线索找回来。", style=MaterialTheme.typography.headlineLarge)
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(query, { query=it }, modifier=Modifier.fillMaxWidth(), placeholder={ Text("上次那件事，后来怎么样了？") })
            Row(verticalAlignment=Alignment.CenterVertically) {
                FilterChip(selected=ai, onClick={ ai=!ai }, label={ Text(if (ai) "AI 回答" else "关键词搜索") })
                TextButton(onClick={ filters=!filters }) { Text("筛选") }
                Spacer(Modifier.weight(1f))
                Button(onClick={ vm.find(query, ai, from, to, person, if (external) "external" else "") }, enabled=query.isNotBlank()) { Text("找回来") }
            }
            if (filters) Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                DateField(from, { from=it }, "开始日期")
                DateField(to, { to=it }, "结束日期")
                OutlinedTextField(person, { person=it }, label={ Text("人物或别名") }, modifier=Modifier.fillMaxWidth())
                Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(external, { external=it }); Text("只查外部知识") }
            }
        }
        if (answer != null) item {
            Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                Text(answer!!.optString("answer"), style=MaterialTheme.typography.bodyLarge)
                Sources(vm, answer!!.optJSONArray("sources") ?: JSONArray())
            }
        }
        if (timeline != null) item {
            Column(verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Text(timeline!!.optString("title"), style=MaterialTheme.typography.titleLarge)
                (timeline!!.optJSONArray("items") ?: JSONArray()).objects().forEach { item ->
                    Text(item.optString("title"), color=Pine)
                    Text(item.optString("text"))
                    Sources(vm, item.optJSONArray("sources") ?: JSONArray())
                }
                TextButton(onClick={ vm.timeline.value=null }) { Text("收起时间线") }
            }
        }
        item { HorizontalDivider(); Spacer(Modifier.height(10.dp)); Text("记忆里的线索", style=MaterialTheme.typography.titleLarge) }
        if (people.isNotEmpty()) item {
            Column { Text("人物 · 未确认的姓名不会自动认定为同一个人", color=Muted)
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    people.forEach { p -> AssistChip(onClick={ person=p.optString("name"); vm.timeline(person=person) },
                        label={ Text(p.optString("name") + if (!p.optBoolean("confirmed")) " · 姓名线索" else "") },
                        trailingIcon={ IconButton(onClick={ personEdit=p }, modifier=Modifier.size(28.dp)) { Icon(Icons.Outlined.Edit, "修改人物与别名", Modifier.size(16.dp)) } }) }
                }
            }
        }
        if (memories.isEmpty()) item { Empty("记忆会慢慢长出来", "留下的事情、灵感和知识，都能成为线索。", Icons.Outlined.ManageSearch) }
        items(memories) { memory ->
            Surface(onClick={ vm.openSource(memory.getJSONObject("source").getString("record_id")) }, color=Color.White.copy(alpha=.65f), shape=RoundedCornerShape(12.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement=Arrangement.spacedBy(6.dp)) {
                    Text(when(memory.optString("kind")) { "decision" -> "决定"; "result" -> "后续结果"; "knowledge" -> "外部知识"; else -> "经历" }, color=Rust, style=MaterialTheme.typography.labelLarge)
                    Text(memory.optString("title"), style=MaterialTheme.typography.titleMedium)
                    Text(memory.optString("detail"), maxLines=3, overflow=TextOverflow.Ellipsis)
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                        (memory.optJSONArray("topics") ?: JSONArray()).strings().forEach { topic -> AssistChip(onClick={ vm.timeline(topic=topic) }, label={ Text(topic) }) }
                    }
                    TextButton(onClick={ memoryEdit=memory }) { Text("修正记忆与线索") }
                }
            }
        }
        item { OutlinedButton(onClick={ vm.weekly() }, modifier=Modifier.fillMaxWidth()) { Text("回顾这一周") } }
    }
    if (memoryEdit != null) {
        val memory=memoryEdit!!
        var title by remember(memory) { mutableStateOf(memory.optString("title")) }
        var detail by remember(memory) { mutableStateOf(memory.optString("detail")) }
        var names by remember(memory) { mutableStateOf((memory.optJSONArray("people") ?: JSONArray()).strings().joinToString("，")) }
        var topics by remember(memory) { mutableStateOf((memory.optJSONArray("topics") ?: JSONArray()).strings().joinToString("，")) }
        AlertDialog(onDismissRequest={ memoryEdit=null }, title={ Text("修正这段记忆") }, text={
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement=Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(title, { title=it }, label={ Text("事情名称") })
                OutlinedTextField(detail, { detail=it }, label={ Text("准确的内容") }, minLines=3)
                OutlinedTextField(names, { names=it }, label={ Text("人物（逗号分隔，重名时写明身份）") })
                OutlinedTextField(topics, { topics=it }, label={ Text("主题线索（逗号分隔）") })
                Text("修正会留下新的原始依据，并更新相关记忆。", color=Muted)
            }
        }, confirmButton={ TextButton(onClick={ vm.editMemory(memory, title, detail, names, topics); memoryEdit=null }, enabled=detail.isNotBlank()) { Text("确认修正") } },
            dismissButton={ TextButton(onClick={ memoryEdit=null }) { Text("取消") } })
    }
    if (personEdit != null) {
        val p=personEdit!!
        var name by remember(p) { mutableStateOf(p.optString("name")) }
        var aliases by remember(p) { mutableStateOf((p.optJSONArray("aliases") ?: JSONArray()).strings().joinToString("，")) }
        var merges by remember(p) { mutableStateOf(setOf<String>()) }
        AlertDialog(onDismissRequest={ personEdit=null }, title={ Text("人物与别名") }, text={
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement=Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(name, { name=it }, label={ Text("名称，重名时注明身份") })
                OutlinedTextField(aliases, { aliases=it }, label={ Text("别名（逗号分隔）") })
                Text("只有确定是同一个人时，才勾选合并：", color=Muted)
                people.filter { it.getString("id")!=p.getString("id") }.forEach { candidate ->
                    val id=candidate.getString("id")
                    Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(id in merges, { checked -> merges=if(checked) merges+id else merges-id }); Text(candidate.optString("name")) }
                }
            }
        }, confirmButton={ TextButton(onClick={ vm.editPerson(p, name, aliases, merges.toList()); personEdit=null }, enabled=name.isNotBlank()) { Text("确认") } },
            dismissButton={ TextButton(onClick={ personEdit=null }) { Text("取消") } })
    }
}

@Composable
fun CompanionScreen(vm: DiaryViewModel) {
    val messages by vm.messages.collectAsState()
    val prefs by vm.prefs.collectAsState()
    var text by remember { mutableStateOf("") }
    val context = LocalContext.current
    val tts = remember { TextToSpeech(context) { } }
    DisposableEffect(Unit) { onDispose { tts.stop(); tts.shutdown() } }
    LazyColumn(Modifier.fillMaxSize(), contentPadding=PaddingValues(22.dp), verticalArrangement=Arrangement.spacedBy(18.dp)) {
        item { Text("与记忆聊聊", style=MaterialTheme.typography.headlineLarge); Spacer(Modifier.height(8.dp)); Text("旧事可以成为线索，追问随时可以跳过。", color=Muted) }
        if (messages.isEmpty()) item { Empty("想起一件事，或聊聊今天", "主动程度可以在“我的”中调整。", Icons.Outlined.Forum) }
        items(messages) { message ->
            Surface(color=if (message.optString("role")=="user") Color(0xFFE4ECE6) else Color.White.copy(alpha=.65f), shape=RoundedCornerShape(12.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text(if (message.optString("role")=="user") "我" else if (message.optString("role")=="reminder") "已确认的提醒" else "拾记", color=Pine, style=MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.height(8.dp)); Text(message.optString("text"), style=MaterialTheme.typography.bodyLarge)
                    Sources(vm, message.optJSONArray("sources") ?: JSONArray())
                    Row {
                        TextButton(onClick={ tts.language=Locale.CHINA; tts.speak(message.optString("text"), TextToSpeech.QUEUE_FLUSH, null, message.optString("id")) }) { Text("朗读") }
                        TextButton(onClick={ tts.stop() }) { Text("停止") }
                    }
                }
            }
        }
        item {
            OutlinedTextField(text, { text=it }, placeholder={ Text("我想聊聊……") }, modifier=Modifier.fillMaxWidth(), minLines=2)
            Row(Modifier.fillMaxWidth(), horizontalArrangement=Arrangement.End) {
                TextButton(onClick={ (context as? app.smartdiary.MainActivity)?.requestRecording() }) { Text("语音补充") }
                TextButton(onClick={ text=""; tts.stop() }) { Text("结束这一轮") }
                Button(onClick={ vm.chat(text); text="" }, enabled=text.isNotBlank()) { Text("发送") }
            }
        }
    }
}

@Composable
fun EntrySheet(vm: DiaryViewModel, entry: Entry, close: () -> Unit) {
    val zone = LocalDiaryZone.current
    val revisions by vm.revisions.collectAsState()
    var history by remember { mutableStateOf(false) }
    var cloudHistory by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var followupOpen by remember { mutableStateOf(false) }
    var followupText by remember { mutableStateOf("") }
    var result by remember { mutableStateOf(false) }
    var followupDate by remember { mutableStateOf(OffsetDateTime.now(zone).toString()) }
    var continuity by remember { mutableStateOf<JSONObject?>(null) }
    var editing by remember(entry.id) { mutableStateOf(false) }
    var text by remember(entry.id, entry.text) { mutableStateOf(entry.text) }
    var occurred by remember(entry.id) { mutableStateOf(Instant.parse(entry.occurredAt).atZone(zone).toOffsetDateTime().toString()) }
    var deleteConfirm by remember { mutableStateOf(false) }
    var privateConfirm by remember { mutableStateOf(false) }
    var player by remember { mutableStateOf<MediaPlayer?>(null) }
    var image by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    val addImage = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) vm.task { val updated=vm.repo.attach(uri, entry); vm.selected.value=updated; vm.notice.value="照片已附到记录" }
    }
    DisposableEffect(Unit) { onDispose { player?.release() } }
    ModalBottomSheet(onDismissRequest=close, containerColor=Paper) {
        Column(Modifier.padding(22.dp).verticalScroll(rememberScrollState()).imePadding(), verticalArrangement=Arrangement.spacedBy(14.dp)) {
            Text("原始记录", style=MaterialTheme.typography.titleLarge)
            Text(shortDate(entry.occurredAt, zone=zone), color=Muted)
            if (editing) {
                OutlinedTextField(text, { text=it }, minLines=4, modifier=Modifier.fillMaxWidth())
                DateTimeField(occurred, { occurred=it }, "事情发生时间", Modifier.fillMaxWidth())
                Button(onClick={ vm.correct(entry, text, occurred); editing=false }) { Text("保存修正") }
            } else Text(entry.text.ifBlank { "原始附件已保存" }, style=MaterialTheme.typography.bodyLarge)
            if (entry.originalText.isNotBlank() && entry.originalText != entry.text) Text("最初输入：${entry.originalText}", color=Muted)
            TextButton(onClick={
                history=!history
                if (history && !entry.private && entry.version>0) vm.task { cloudHistory=vm.repo.api.array("/v1/records/${entry.id}/revisions").objects() }
            }) { Text("查看修改版本") }
            if (history) {
                val versions=cloudHistory + revisions.filter { it.optString("record_id")==entry.id }
                if (versions.isEmpty()) Text("尚无修改版本", color=Muted)
                versions.forEach { revision -> Text("版本 ${revision.optInt("version")} · ${revision.optJSONObject("snapshot")?.optString("text").orEmpty()}", color=Muted) }
            }
            if (entry.error.isNotBlank()) Text(entry.error, color=Rust)
            Row {
                TextButton(onClick={ followupOpen=true }) { Text("补充后续或结果") }
                if (!entry.private && entry.version>0) TextButton(onClick={ vm.task { continuity=vm.repo.api.json("/v1/records/${entry.id}/timeline") } }) { Text("连续记录") }
            }
            continuity?.optJSONArray("items")?.objects()?.forEach { item ->
                Text(item.optString("title") + " · " + item.optString("text"), color=Muted)
                Sources(vm, item.optJSONArray("sources") ?: JSONArray())
            }
            if (!entry.private && entry.text.isNotBlank() && vm.repo.token.isNotEmpty() && !entry.dirty && entry.supersededBy.isBlank()) TextButton(onClick={ vm.chat("我想聊聊这条记录。", entry.id); close() }) { Text("用这段记录继续聊") }
            Row(verticalAlignment=Alignment.CenterVertically) {
                Checkbox(entry.sourceType=="external", { external -> vm.task {
                    val updated=entry.copy(sourceType=if(external) "external" else "personal", dirty=true)
                    vm.repo.save(updated); vm.repo.clearDerived(); vm.selected.value=updated
                } })
                Text("这是摘录或外部知识")
            }
            OutlinedButton(onClick={ addImage.launch("image/*") }) { Icon(Icons.Outlined.AddPhotoAlternate, null); Text("  附上一张照片") }
            entry.media.forEachIndexed { index, media ->
                OutlinedButton(onClick={ vm.task {
                    if (media.mime.startsWith("image/")) {
                        val bytes = vm.repo.mediaBytes(media)
                        val bounds=android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds=true }
                        android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                        var sample=1
                        while(maxOf(bounds.outWidth, bounds.outHeight)/sample>2048) sample*=2
                        image=android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, android.graphics.BitmapFactory.Options().apply { inSampleSize=sample })
                        bytes.fill(0)
                    } else {
                        player?.release()
                        val bytes=vm.repo.mediaBytes(media)
                        val dataSource=object: android.media.MediaDataSource() {
                            override fun getSize()=bytes.size.toLong()
                            override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
                                if (position>=bytes.size) return -1
                                val count=minOf(size, bytes.size-position.toInt())
                                bytes.copyInto(buffer, offset, position.toInt(), position.toInt()+count)
                                return count
                            }
                            override fun close() { bytes.fill(0) }
                        }
                        player = MediaPlayer().apply { setDataSource(dataSource); prepare(); start(); setOnCompletionListener { it.release(); player=null } }
                    }
                } }) { Icon(if (media.mime.startsWith("image/")) Icons.Outlined.Image else Icons.Outlined.PlayArrow, null); Spacer(Modifier.width(8.dp)); Text(if (media.mime.startsWith("image/")) "查看照片 ${index+1}" else "回放片段 ${index+1} · ${media.duration.toInt()} 秒") }
            }
            if (player != null) TextButton(onClick={ player?.release(); player=null }) { Text("停止回放") }
            image?.let { Image(it.asImageBitmap(), "原始照片", Modifier.fillMaxWidth().heightIn(max=360.dp)) }
            if (entry.conflict != null) {
                Text("另一份版本", style=MaterialTheme.typography.titleMedium)
                Text(entry.conflict.optString("text"))
                Row { TextButton(onClick={ vm.task { vm.repo.resolve(entry, true); close() } }) { Text("使用手机版本") }; TextButton(onClick={ vm.task { vm.repo.resolve(entry, false); close() } }) { Text("使用云端版本") } }
            }
            Row { TextButton(onClick={ editing=!editing }) { Text("修改文字或时间") }
                if (!entry.private) TextButton(onClick={ privateConfirm=true }) { Text("改为仅本地") } }
            Row { if (!entry.private && vm.repo.token.isNotEmpty()) TextButton(onClick={ vm.task { vm.repo.api.json("/v1/records/${entry.id}/retry", "POST"); vm.repo.sync() } }) { Text("重新处理") }
                TextButton(onClick={ deleteConfirm=true }) { Text("删除", color=Rust) } }
            Spacer(Modifier.height(24.dp))
        }
    }
    if (deleteConfirm) AlertDialog(onDismissRequest={ deleteConfirm=false }, title={ Text("删除这条记录？") },
        text={ Text("原始内容、附件和关联的云端记忆将删除。离线时会在联网后完成云端清理。") },
        confirmButton={ TextButton(onClick={ vm.task { vm.repo.delete(entry); close() }; deleteConfirm=false }) { Text("删除", color=Rust) } }, dismissButton={ TextButton(onClick={ deleteConfirm=false }) { Text("取消") } })
    if (privateConfirm) AlertDialog(onDismissRequest={ privateConfirm=false }, title={ Text("只保存在手机上？") },
        text={ Text("保留手机上的内容，并在联网后删除云端副本和关联记忆。") },
        confirmButton={ TextButton(onClick={ vm.task { vm.repo.makePrivate(entry); close() }; privateConfirm=false }) { Text("确认") } }, dismissButton={ TextButton(onClick={ privateConfirm=false }) { Text("取消") } })
    if (followupOpen) AlertDialog(onDismissRequest={ followupOpen=false }, title={ Text("让这件事继续留下来") },
        text={ Column(verticalArrangement=Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(followupText, { followupText=it }, placeholder={ Text("后来发生了什么？结果如何？") }, minLines=3)
            Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(result, { result=it }); Text("这是决定的后续结果") }
            DateTimeField(followupDate, { followupDate=it }, "发生时间")
        } },
        confirmButton={ TextButton(enabled=followupText.isNotBlank(), onClick={ vm.task {
            vm.repo.save(Entry(text=followupText.trim(), private=entry.private, sourceType=entry.sourceType,
                occurredAt=OffsetDateTime.parse(followupDate).toInstant().toString(), parentRecordId=entry.id, relation=if(result) "result" else "followup"))
            vm.notice.value="后续已保存到手机"; followupOpen=false
        } }) { Text("保存后续") } }, dismissButton={ TextButton(onClick={ followupOpen=false }) { Text("取消") } })
}
