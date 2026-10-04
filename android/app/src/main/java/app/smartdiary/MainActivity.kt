package app.smartdiary

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Build
import android.view.WindowManager
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.FragmentActivity
import androidx.biometric.BiometricPrompt
import androidx.biometric.BiometricManager
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import app.smartdiary.ui.DiaryViewModel
import app.smartdiary.ui.DiaryRoot
import app.smartdiary.recording.RecordingService
import kotlinx.coroutines.flow.MutableStateFlow

class MainActivity : FragmentActivity() {
    lateinit var vm: DiaryViewModel
    private val unlocked = MutableStateFlow(false)
    private val action = MutableStateFlow("")
    private val microphone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRecording() else vm.notice.value = "需要麦克风权限才能录音，也可以先写一笔。"
    }
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!BuildConfig.DEBUG) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        vm = ViewModelProvider(this)[DiaryViewModel::class.java]
        setContent { DiaryRoot(vm, unlocked, action, ::requestRecording, ::unlock) }
        handle(intent)
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handle(intent) }
    override fun onResume() {
        super.onResume()
        if (::vm.isInitialized) {
            if (vm.repo.appLock) unlock() else unlocked.value = true
            vm.repo.requestSync()
        }
    }
    override fun onStop() {
        super.onStop()
        if (::vm.isInitialized && vm.repo.appLock) unlocked.value = false
        java.io.File(cacheDir, "preview").listFiles()?.filter { System.currentTimeMillis()-it.lastModified()>600000 }?.forEach { it.delete() }
    }
    private fun handle(intent: Intent) {
        action.value = intent.action.orEmpty()
        if (intent.action == Intent.ACTION_SEND) {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
            if (text.isNotBlank()) {
                vm.updateDraft(text)
                action.value = "app.smartdiary.SHARE"
            }
            val uri = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, android.net.Uri::class.java)
                      else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_STREAM)
            if (uri != null) vm.task { vm.repo.attach(uri); vm.notice.value = "图片已保存到手机" }
        }
    }
    fun requestRecording() {
        if (RecordingService.state.value.active) { action.value = "app.smartdiary.RECORD"; return }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startRecording()
        else microphone.launch(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
    private fun startRecording() {
        ContextCompat.startForegroundService(this, Intent(this, RecordingService::class.java).setAction("start"))
    }
    fun unlock() {
        if (!vm.repo.appLock) { unlocked.value = true; return }
        val authenticators = if (Build.VERSION.SDK_INT >= 30) BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.DEVICE_CREDENTIAL else BiometricManager.Authenticators.BIOMETRIC_STRONG
        if (BiometricManager.from(this).canAuthenticate(authenticators) != BiometricManager.BIOMETRIC_SUCCESS) {
            vm.notice.value = "请先在系统中设置指纹或设备锁。"
            return
        }
        val info = BiometricPrompt.PromptInfo.Builder().setTitle("打开拾记").setSubtitle("你的记录只在解锁后显示")
            .setAllowedAuthenticators(authenticators).apply { if (Build.VERSION.SDK_INT < 30) setNegativeButtonText("取消") }.build()
        BiometricPrompt(this, ContextCompat.getMainExecutor(this), object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) { unlocked.value = true }
        }).authenticate(info)
    }
}
