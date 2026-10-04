package app.smartdiary.data

import android.content.Context
import android.app.NotificationChannel
import android.app.NotificationManager
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import app.smartdiary.DiaryApp
import app.smartdiary.R
import java.time.LocalTime

class SyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val repo = (applicationContext as DiaryApp).repo
        return try {
            repo.sync()
            val prefs = repo.dao.get(repo.owner.value, "settings", "preferences")?.let(repo::decode)
            if (prefs?.optBoolean("notifications", true) != false) {
                val zone = java.time.ZoneId.of(prefs?.optString("timezone", "Asia/Shanghai") ?: "Asia/Shanghai")
                val now = java.time.ZonedDateTime.now(zone).toLocalTime()
                val start = LocalTime.parse(prefs?.optString("quiet_start", "22:00") ?: "22:00")
                val end = LocalTime.parse(prefs?.optString("quiet_end", "08:00") ?: "08:00")
                val quiet = if (start == end) false else if (start < end) now >= start && now < end else now >= start || now < end
                if (!quiet) {
                    val messages = repo.dao.list(repo.owner.value, "message")
                    val latest = messages.map(repo::decode).filter { it.optString("role")=="reminder" ||
                        (it.optBoolean("proactive") && prefs?.optString("proactivity")!="quiet") }
                        .maxByOrNull { java.time.Instant.parse(it.optString("created_at")) }
                    val remembered = repo.dao.get(repo.owner.value, "notification", "last")?.let(repo::decode)?.optString("id")
                    if (latest != null && latest.optString("id") != remembered && latest.optString("role") != "user") {
                        val manager = applicationContext.getSystemService(NotificationManager::class.java)
                        manager.createNotificationChannel(NotificationChannel("updates", "回顾与提醒", NotificationManager.IMPORTANCE_DEFAULT))
                        val intent = android.app.PendingIntent.getActivity(applicationContext, 4,
                            android.content.Intent(applicationContext, app.smartdiary.MainActivity::class.java).setAction("app.smartdiary.INBOX"),
                            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
                        try {
                            manager.notify(4, NotificationCompat.Builder(applicationContext, "updates").setSmallIcon(R.drawable.ic_diary)
                                .setContentTitle("拾记").setContentText("有一条新的回顾或提醒，打开查看。")
                                .setContentIntent(intent).setAutoCancel(true).build())
                        } catch (_: SecurityException) { }
                        repo.put("notification", "last", org.json.JSONObject().put("id", latest.getString("id")))
                    }
                }
            }
            Result.success()
        } catch (e: ApiException) {
            if (e.code == 401) Result.failure() else Result.retry()
        } catch (_: Exception) { Result.retry() }
    }
}
