package app.smartdiary.recording

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class WavTest {
    @Test fun pcmSegmentHasPlayableHeader() {
        val pcm = ByteArray(32000)
        val data = Wav.encode(pcm)
        assertEquals("RIFF", String(data.copyOfRange(0, 4)))
        assertEquals("WAVE", String(data.copyOfRange(8, 12)))
        assertEquals(16000, ByteBuffer.wrap(data, 24, 4).order(ByteOrder.LITTLE_ENDIAN).int)
        assertEquals(pcm.size, ByteBuffer.wrap(data, 40, 4).order(ByteOrder.LITTLE_ENDIAN).int)
        assertArrayEquals(pcm, data.copyOfRange(44, data.size))
    }
    @Test(expected=IllegalArgumentException::class) fun rejectsPartialSample() { Wav.encode(byteArrayOf(1)) }
}
