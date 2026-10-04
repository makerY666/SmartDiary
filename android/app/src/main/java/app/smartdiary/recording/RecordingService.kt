package app.smartdiary.recording

import android.app.Service
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioRecord
import android.media.AudioFormat
import android.media.MediaRecorder
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import app.smartdiary.DiaryApp
import app.smartdiary.MainActivity
import app.smartdiary.R
import app.smartdiary.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.*
import java.util.UUID

data class RecordingState(val active: Boolean = false, val paused: Boolean = false,
                          val seconds: Int = 0, val recordId: String = "", val error: String = "")

class RecordingService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var audio: AudioRecord? = null
    @Volatile private var running = false
    @Volatile private var paused = false
    private lateinit var repo: Repository
    override fun onCreate() {
        super.onCreate()
        repo = (application as DiaryApp).repo
    }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            "start" -> if (!running) start()
            "pause" -> { paused = true; state.value = state.value.copy(paused=true) }
            "resume" -> { paused = false; state.value = state.value.copy(paused=false) }
            "stop" -> { running = false }
        }
        return START_NOT_STICKY
    }
    private fun start() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("recording", "正在录音", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, RecordingService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, "recording").setSmallIcon(R.drawable.ic_diary)
            .setContentTitle("正在留下这一刻").setContentText("录音已分段保存在手机上")
            .setContentIntent(open).setOngoing(true).addAction(0, "结束", stop).build()
        ServiceCompat.startForeground(this, 1, notification, if (android.os.Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
        running = true
        paused = false
        scope.launch {
            val entry = Entry(kind="audio")
            var journal: File? = null
            try {
                repo.save(entry)
                state.value = RecordingState(true, false, 0, entry.id)
                val bufferSize = maxOf(AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT), 32000)
                audio = AudioRecord(MediaRecorder.AudioSource.MIC, 16000, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, bufferSize * 2)
                require(audio!!.state == AudioRecord.STATE_INITIALIZED) { "麦克风无法初始化" }
                audio!!.startRecording()
                val buffer = ByteArray(32000)
                var chunkBytes = 0
                var totalBytes = 0
                while (running) {
                    if (paused) {
                        if (journal != null) { finalizeJournal(repo, journal!!); journal = null; chunkBytes = 0 }
                        if (audio!!.recordingState == AudioRecord.RECORDSTATE_RECORDING) audio!!.stop()
                        delay(150)
                        continue
                    }
                    if (audio!!.recordingState != AudioRecord.RECORDSTATE_RECORDING) audio!!.startRecording()
                    val read = audio!!.read(buffer, 0, buffer.size)
                    if (read < 0) throw IOException("录音已中断")
                    if (read == 0) continue
                    if (journal == null) {
                        journal = File(filesDir, "journals/${entry.id}_${UUID.randomUUID()}.journal").apply { parentFile?.mkdirs() }
                    }
                    val encrypted = repo.crypto.encrypt(buffer.copyOf(read))
                    FileOutputStream(journal, true).use { file ->
                        val stream = DataOutputStream(file)
                        stream.writeInt(encrypted.size); stream.write(encrypted); stream.flush(); file.fd.sync()
                    }
                    chunkBytes += read; totalBytes += read
                    state.value = state.value.copy(seconds=totalBytes / 32000, paused=paused)
                    if (chunkBytes >= 32000 * 30) { finalizeJournal(repo, journal!!); journal = null; chunkBytes = 0 }
                }
                if (journal != null) finalizeJournal(repo, journal!!)
                state.value = RecordingState(recordId=entry.id)
            } catch (_: Exception) {
                state.value = state.value.copy(active=false, error="录音中断，已保存的片段可恢复")
                journal?.let { runCatching { finalizeJournal(repo, it) } }
            } finally {
                running = false
                runCatching { audio?.stop() }; audio?.release(); audio = null
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                repo.requestSync()
            }
        }
    }
    override fun onDestroy() { running = false; super.onDestroy() }
    companion object {
        val state = MutableStateFlow(RecordingState())
        suspend fun finalizeJournal(repo: Repository, journal: File) {
            val parts = journal.name.removeSuffix(".journal").split('_')
            if (parts.size != 2) return
            val entry = repo.entry(parts[0])
            if (entry==null || entry.deleted) { journal.delete(); return }
            val pcm = ByteArrayOutputStream()
            DataInputStream(FileInputStream(journal)).use { input ->
                while (true) {
                    val size = try { input.readInt() } catch (_: EOFException) { break }
                    if (size !in 1..100000) break
                    val encrypted = ByteArray(size)
                    try { input.readFully(encrypted) } catch (_: EOFException) { break }
                    val bytes = runCatching { repo.crypto.decrypt(encrypted) }.getOrNull() ?: break
                    pcm.write(bytes)
                }
            }
            if (pcm.size() > 0) {
                val media = Media(parts[1], "audio/wav", pcm.size() / 32000.0)
                repo.storeMedia(media.id, Wav.encode(pcm.toByteArray()))
                repo.appendMedia(entry.id, media)
            }
            journal.delete()
        }
        suspend fun recover(repo: Repository) {
            File(repo.context.filesDir, "journals").listFiles()?.filter { it.name.endsWith(".journal") }?.forEach {
                runCatching { finalizeJournal(repo, it) }
            }
        }
    }
}
