package app.smartdiary.data

import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, val body: JSONObject) : IOException(
    if (body.opt("detail") is String) body.optString("detail") else body.optJSONObject("detail")?.optString("message") ?: "请求失败（$code）"
)

class Api(private val repo: Repository) {
    private val client = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(120, TimeUnit.SECONDS).build()
    private fun call(path: String, method: String, body: RequestBody? = null): ByteArray {
        val base = repo.serverUrl.trimEnd('/')
        require(base.startsWith("https://") || (app.smartdiary.BuildConfig.DEBUG && base.startsWith("http://"))) { "服务器地址必须使用 HTTPS" }
        val request = Request.Builder().url(base + path).apply {
            if (repo.token.isNotEmpty()) header("Authorization", "Bearer ${repo.token}")
            if (method != "GET") method(method, body ?: ByteArray(0).toRequestBody())
        }.build()
        client.newCall(request).execute().use { response ->
            val data = response.body?.bytes() ?: ByteArray(0)
            if (!response.isSuccessful) throw ApiException(response.code, runCatching { JSONObject(String(data, Charsets.UTF_8)) }.getOrDefault(JSONObject()))
            return data
        }
    }
    fun json(path: String, method: String = "GET", body: JSONObject? = null): JSONObject =
        JSONObject(String(call(path, method, body?.toString()?.toRequestBody("application/json; charset=utf-8".toMediaType())), Charsets.UTF_8))
    fun array(path: String) = org.json.JSONArray(String(call(path, "GET"), Charsets.UTF_8))
    fun bytes(path: String) = call(path, "GET")
    fun upload(path: String, bytes: ByteArray, mime: String, name: String, duration: Double = 0.0, position: Int = 0, preserveText: Boolean = false): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("duration_seconds", duration.toString())
            .addFormDataPart("position", position.toString())
            .addFormDataPart("preserve_text", preserveText.toString())
            .addFormDataPart("file", name, bytes.toRequestBody(mime.toMediaType())).build()
        return JSONObject(String(call(path, "POST", body), Charsets.UTF_8))
    }
}
