package app.smartdiary

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.smartdiary.data.*
import app.smartdiary.recording.RecordingService
import app.smartdiary.recording.Wav
import kotlinx.coroutines.runBlocking
import org.junit.*
import org.junit.runner.RunWith
import java.io.*
import java.util.UUID

/** Run on a fresh debug installation, so the tests cannot touch a connected diary account. */
@RunWith(AndroidJUnit4::class)
class OfflineCaptureTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val created = mutableListOf<String>()
    private val repo get() = (compose.activity.application as DiaryApp).repo

    @Before fun isolatedGuest() { Assume.assumeTrue("Requires a fresh guest installation", repo.owner.value=="guest") }
    @After fun cleanup() = runBlocking { created.forEach { id -> repo.entry(id)?.let { repo.delete(it) } } }

    @Test fun textSurvivesActivityRecreation() {
        val note = "离线记录验证-${UUID.randomUUID()}"
        compose.onNodeWithText("写一笔").performClick()
        compose.onNode(hasSetTextAction()).performTextInput(note)
        compose.onNodeWithText("保存到手机").performClick()
        compose.waitUntil(10000) { compose.onAllNodesWithText(note).fetchSemanticsNodes().isNotEmpty() }
        runBlocking { repo.dao.list(repo.owner.value, "record").map { Entry.from(repo.decode(it)) }.find { it.text==note }?.let { created.add(it.id) } }
        compose.activityRule.scenario.recreate()
        compose.waitUntil(10000) { compose.onAllNodesWithText(note).fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun encryptedJournalRecoversOnlyCompleteFrames() = runBlocking {
        val entry=Entry(kind="audio", private=true, version=1, text="原文", dirty=false)
        created.add(entry.id)
        repo.save(entry)
        repo.save(entry.copy(text="用户修正", dirty=true))
        val mediaId=UUID.randomUUID().toString()
        val journal=File(repo.context.filesDir, "journals/${entry.id}_$mediaId.journal").apply { parentFile?.mkdirs() }
        val pcm=ByteArray(32000) { (it % 100).toByte() }
        val encrypted=repo.crypto.encrypt(pcm)
        DataOutputStream(FileOutputStream(journal)).use { out -> out.writeInt(encrypted.size); out.write(encrypted); out.writeInt(100); out.write(byteArrayOf(1,2,3)) }
        RecordingService.finalizeJournal(repo, journal)
        val restored=repo.entry(entry.id)!!
        Assert.assertEquals(1, restored.media.size)
        Assert.assertEquals("用户修正", restored.text)
        Assert.assertTrue(restored.dirty)
        Assert.assertArrayEquals(Wav.encode(pcm), repo.readMedia(mediaId))
        Assert.assertFalse(repo.mediaFile(mediaId).readBytes().contentEquals(Wav.encode(pcm)))
        Assert.assertFalse(journal.exists())
    }

    @Test fun restoringAnOldExportCannotResurrectADeletedRecord() = runBlocking {
        val entry=Entry(text="只在本地的恢复验证", private=true)
        created.add(entry.id)
        repo.save(entry)
        val archive=ByteArrayOutputStream().also { repo.exportLocal(it) }.toByteArray()
        repo.delete(entry)
        repo.restoreLocal(ByteArrayInputStream(archive))
        Assert.assertTrue(repo.entry(entry.id)?.deleted != false)
        Assert.assertNotNull(repo.dao.get(repo.owner.value, "tombstone", entry.id))
    }
}
