package app.smartdiary.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.IconButton
import androidx.compose.material.icons.outlined.Close
import androidx.compose.foundation.layout.Row
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

val LocalDiaryZone = compositionLocalOf { ZoneId.of("Asia/Shanghai") }

@Composable
fun DateField(value: String, change: (String) -> Unit, label: String) {
    val context=LocalContext.current
    val zone=LocalDiaryZone.current
    val date=runCatching { java.time.LocalDate.parse(value) }.getOrDefault(java.time.LocalDate.now(zone))
    Row {
        OutlinedButton(onClick={ DatePickerDialog(context, { _, year, month, day ->
            change(java.time.LocalDate.of(year, month+1, day).toString())
        }, date.year, date.monthValue-1, date.dayOfMonth).show() }) {
            Text(label + " · " + if(value.isBlank()) "不限" else date.format(DateTimeFormatter.ofPattern("yyyy年M月d日")))
        }
        if(value.isNotBlank()) IconButton(onClick={ change("") }) { Icon(Icons.Outlined.Close, "清除$label") }
    }
}

@Composable
fun DateTimeField(value: String, change: (String) -> Unit, label: String, modifier: Modifier = Modifier,
                  zone: ZoneId = LocalDiaryZone.current) {
    val context = LocalContext.current
    val time = runCatching { OffsetDateTime.parse(value).atZoneSameInstant(zone) }.getOrDefault(java.time.ZonedDateTime.now(zone))
    OutlinedButton(onClick={
        DatePickerDialog(context, { _, year, month, day ->
            TimePickerDialog(context, { _, hour, minute ->
                change(java.time.LocalDateTime.of(year, month+1, day, hour, minute).atZone(zone).toOffsetDateTime().toString())
            }, time.hour, time.minute, true).show()
        }, time.year, time.monthValue-1, time.dayOfMonth).show()
    }, modifier=modifier) {
        Icon(Icons.Outlined.Schedule, null)
        Text("  $label · " + if (value.isBlank()) "选择日期与时间" else time.format(DateTimeFormatter.ofPattern("M月d日 HH:mm")))
    }
}
