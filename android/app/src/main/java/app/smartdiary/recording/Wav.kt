package app.smartdiary.recording

import java.nio.ByteBuffer
import java.nio.ByteOrder

object Wav {
    fun encode(pcm: ByteArray, rate: Int = 16000): ByteArray {
        require(pcm.size % 2 == 0)
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray()).putInt(36 + pcm.size).put("WAVEfmt ".toByteArray())
        header.putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(16)
        header.put("data".toByteArray()).putInt(pcm.size)
        return header.array() + pcm
    }
}
