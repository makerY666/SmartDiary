package app.smartdiary

import android.app.Application
import app.smartdiary.data.Repository
import app.smartdiary.recording.RecordingService
import kotlinx.coroutines.*

class DiaryApp : Application() {
    lateinit var repo: Repository
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    override fun onCreate() {
        super.onCreate()
        repo = Repository(this)
        repo.periodicSync()
        scope.launch { RecordingService.recover(repo) }
    }
}
