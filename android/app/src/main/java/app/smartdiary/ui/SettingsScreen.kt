package app.smartdiary.ui

import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import app.smartdiary.data.*
import org.json.JSONObject

@Composable
fun SettingsScreen(vm: DiaryViewModel) {
    val context = LocalContext.current
    val prefs by vm.prefs.collectAsState()
    val budget by vm.budget.collectAsState()
    val owner by vm.repo.owner.collectAsState()
    val reminders by vm.reminders.collectAsState()
    var server by remember { mutableStateOf(vm.repo.serverUrl) }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var invitation by remember { mutableStateOf("") }
    var registering by remember { mutableStateOf(false) }
    var connecting by remember { mutableStateOf(owner=="guest") }
    var diaryTime by remember(prefs.toString()) { mutableStateOf(prefs.optString("diary_time", "22:30")) }
    var zone by remember(prefs.toString()) { mutableStateOf(prefs.optString("timezone", "Asia/Shanghai")) }
    var quietStart by remember(prefs.toString()) { mutableStateOf(prefs.optString("quiet_start", "22:00")) }
    var quietEnd by remember(prefs.toString()) { mutableStateOf(prefs.optString("quiet_end", "08:00")) }
    var limit by remember(prefs.toString()) { mutableStateOf(prefs.optInt("proactive_limit", 1).toString()) }
    var lock by remember { mutableStateOf(vm.repo.appLock) }
    var reminderOpen by remember { mutableStateOf(false) }
    var reminderText by remember { mutableStateOf("") }
    var reminderDate by remember { mutableStateOf("") }
    var backupConfirm by remember { mutableStateOf(false) }
    var cloudExport by remember { mutableStateOf(false) }
    var cloudRestore by remember { mutableStateOf(false) }
    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) vm.task {
            context.contentResolver.openOutputStream(uri)!!.use { stream ->
                if (cloudExport) stream.write(vm.repo.api.bytes("/v1/export")) else vm.repo.exportLocal(stream)
            }
            vm.notice.value = "导出完成，请妥善保存备份"
        }
    }
    val restore = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) vm.task {
            if (cloudRestore) {
                val data=context.contentResolver.openInputStream(uri)!!.use { input ->
                    val out=java.io.ByteArrayOutputStream()
                    val bytes=ByteArray(8192)
                    while (true) { val n=input.read(bytes); if(n<0) break; require(out.size()+n<=100*1024*1024) { "备份超过 100MB" }; out.write(bytes,0,n) }
                    out.toByteArray()
                }
                val result=vm.repo.api.upload("/v1/restore", data, "application/zip", "restore.zip")
                vm.repo.sync()
                vm.notice.value="恢复 ${result.optInt("restored")} 条云端记录"
            } else {
                val count = context.contentResolver.openInputStream(uri)!!.use { vm.repo.restoreLocal(it) }
                vm.notice.value = "恢复 $count 条记录，已删除或已存在的内容会跳过"
            }
        }
    }
    Column(Modifier.fillMaxSize().padding(horizontal=22.dp).verticalScroll(rememberScrollState()), verticalArrangement=Arrangement.spacedBy(16.dp)) {
        Text("让它适合你的生活。", style=MaterialTheme.typography.headlineLarge)
        Text(if (owner=="guest") "手机上的记录随时可用；连接同步后启用自动整理与回查。" else "已连接个人账户，记录先保存到手机。", color=Muted)
        TextButton(onClick={ connecting=!connecting }) { Text(if (connecting) "收起连接设置" else "账户与同步") }
        if (connecting) {
            OutlinedTextField(server, { server=it }, label={ Text("同步服务地址") }, modifier=Modifier.fillMaxWidth())
            OutlinedTextField(username, { username=it }, label={ Text("用户名") }, modifier=Modifier.fillMaxWidth())
            OutlinedTextField(password, { password=it }, label={ Text("密码（至少 10 位）") }, visualTransformation=PasswordVisualTransformation(), modifier=Modifier.fillMaxWidth())
            Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(registering, { registering=it }); Text("创建新账户") }
            if (registering) OutlinedTextField(invitation, { invitation=it }, label={ Text("内测邀请码") }, modifier=Modifier.fillMaxWidth())
            Button(onClick={ vm.repo.serverUrl=server; vm.task { vm.repo.login(username, password, invitation, registering); vm.repo.sync(); password=""; connecting=false; vm.notice.value="账户已连接" } }, enabled=password.length>=10 && username.length>=3) { Text(if (registering) "创建并连接" else "登录并连接") }
        }
        HorizontalDivider()
        Text("主动程度", style=MaterialTheme.typography.titleLarge)
        for ((mode, title, description) in listOf(Triple("quiet", "安静", "只自动生成日记，其他能力由你打开。"),
            Triple("balanced", "适度", "少量补记、回顾和旧记忆提示。"), Triple("companion", "伙伴", "联系旧记忆，回应和追问；随时可跳过。"))) {
            Surface(onClick={ val value=JSONObject(prefs.toString()).put("proactivity", mode).put("proactive_limit", if (mode=="quiet") 0 else if(mode=="companion") 2 else 1); vm.setPrefs(value) }, color=if(prefs.optString("proactivity")==mode) MaterialTheme.colorScheme.surfaceVariant else Paper) {
                Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment=Alignment.CenterVertically) {
                    RadioButton(prefs.optString("proactivity")==mode, onClick=null)
                    Column(Modifier.padding(start=8.dp)) { Text(title, style=MaterialTheme.typography.titleMedium); Text(description, color=Muted) }
                }
            }
        }
        OutlinedTextField(diaryTime, { diaryTime=it }, label={ Text("日记整理时间 HH:mm") }, modifier=Modifier.fillMaxWidth())
        OutlinedTextField(zone, { zone=it }, label={ Text("时区") }, modifier=Modifier.fillMaxWidth())
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(quietStart, { quietStart=it }, label={ Text("免打扰开始") }, modifier=Modifier.weight(1f))
            OutlinedTextField(quietEnd, { quietEnd=it }, label={ Text("免打扰结束") }, modifier=Modifier.weight(1f))
        }
        OutlinedTextField(limit, { limit=it }, label={ Text("每天主动提示上限（0—10）") }, modifier=Modifier.fillMaxWidth())
        OutlinedButton(onClick={ vm.setPrefs(JSONObject(prefs.toString()).put("diary_time", diaryTime).put("timezone", zone)
            .put("quiet_start", quietStart).put("quiet_end", quietEnd).put("proactive_limit", limit.toIntOrNull() ?: -1)) }) { Text("保存时间与频率") }
        Row(Modifier.fillMaxWidth(), verticalAlignment=Alignment.CenterVertically, horizontalArrangement=Arrangement.SpaceBetween) {
            Text("回顾与提醒通知")
            Switch(prefs.optBoolean("notifications", true), onCheckedChange={ vm.setPrefs(JSONObject(prefs.toString()).put("notifications", it)) })
        }
        HorizontalDivider()
        Text("数据由你掌控", style=MaterialTheme.typography.titleLarge)
        Row(Modifier.fillMaxWidth(), verticalAlignment=Alignment.CenterVertically, horizontalArrangement=Arrangement.SpaceBetween) {
            Text("应用锁")
            Switch(lock, onCheckedChange={ value ->
                val authenticators = if(Build.VERSION.SDK_INT>=30) BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.DEVICE_CREDENTIAL else BiometricManager.Authenticators.BIOMETRIC_STRONG
                if (!value || BiometricManager.from(context).canAuthenticate(authenticators)==BiometricManager.BIOMETRIC_SUCCESS) { lock=value; vm.repo.appLock=value }
                else vm.notice.value="请先设置系统指纹或设备锁。"
            })
        }
        Text("通知隐藏正文。本地内容和附件加密保存；仅本地记录不会交给云端处理。", color=Muted)
        OutlinedButton(onClick={ cloudExport=false; backupConfirm=true }, modifier=Modifier.fillMaxWidth()) { Text("导出手机数据与原始附件") }
        if (owner!="guest") OutlinedButton(onClick={ cloudExport=true; backupConfirm=true }, modifier=Modifier.fillMaxWidth()) { Text("导出完整云端数据") }
        OutlinedButton(onClick={ cloudRestore=false; restore.launch(arrayOf("application/zip", "application/octet-stream")) }, modifier=Modifier.fillMaxWidth()) { Text("恢复手机备份") }
        if (owner!="guest") OutlinedButton(onClick={ cloudRestore=true; restore.launch(arrayOf("application/zip", "application/octet-stream")) }, modifier=Modifier.fillMaxWidth()) { Text("恢复云端备份") }
        HorizontalDivider()
        Text("待跟进的事情", style=MaterialTheme.typography.titleLarge)
        reminders.filterNot { it.optBoolean("completed") }.forEach { item ->
            Row(Modifier.fillMaxWidth(), verticalAlignment=Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) { Text(item.optString("text")); Text(runCatching { java.time.OffsetDateTime.parse(item.optString("due_at")).atZoneSameInstant(java.time.ZoneId.of(prefs.optString("timezone", "Asia/Shanghai"))).format(java.time.format.DateTimeFormatter.ofPattern("M月d日 HH:mm")) }.getOrDefault(item.optString("due_at")), color=Muted) }
                TextButton(onClick={ vm.task { vm.repo.api.json("/v1/reminders/${item.getString("id")}/complete", "POST"); vm.repo.sync() } }) { Text("完成") }
            }
        }
        OutlinedButton(onClick={ reminderOpen=true }) { Text("添加并确认提醒") }
        budget?.let { value ->
            HorizontalDivider()
            Text("本月运行用量", style=MaterialTheme.typography.titleLarge)
            Text("预计 ${value.optDouble("projected_yuan")} / ${value.optDouble("limit_yuan", 300.0)} 元", color=if (value.optBoolean("warning")) Rust else Pine)
            Text("费用按配置估算，实际以账单为准。", color=Muted, style=MaterialTheme.typography.bodyMedium)
            if (!value.optBoolean("ai_enabled")) Text("云端智能整理尚未启用，原始记录和搜索仍可用。", color=Muted)
        }
        Spacer(Modifier.height(32.dp))
    }
    if (backupConfirm) AlertDialog(onDismissRequest={ backupConfirm=false }, title={ Text("导出可迁移备份") },
        text={ Text("导出包包含可读的日记和原始附件。请保存在你信任的位置。") },
        confirmButton={ TextButton(onClick={ backupConfirm=false; export.launch("smartdiary-${java.time.LocalDate.now()}.zip") }) { Text("选择保存位置") } },
        dismissButton={ TextButton(onClick={ backupConfirm=false }) { Text("取消") } })
    if (reminderOpen) AlertDialog(onDismissRequest={ reminderOpen=false }, title={ Text("确认提醒内容与时间") },
        text={ Column(verticalArrangement=Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(reminderText, { reminderText=it }, label={ Text("提醒什么") })
            DateTimeField(reminderDate, { reminderDate=it }, "提醒时间")
            Text("提醒可能因手机后台调度而延迟，打开 App 后补齐。", color=Muted)
        } }, confirmButton={ TextButton(onClick={ vm.reminder(reminderText, reminderDate); reminderOpen=false }, enabled=reminderText.isNotBlank() && reminderDate.isNotBlank()) { Text("确认并创建") } },
        dismissButton={ TextButton(onClick={ reminderOpen=false }) { Text("取消") } })
}
