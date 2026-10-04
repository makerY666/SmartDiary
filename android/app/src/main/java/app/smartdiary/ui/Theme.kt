package app.smartdiary.ui

import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

val Paper = Color(0xFFF7F5EE)
val Ink = Color(0xFF263A36)
val Pine = Color(0xFF24584F)
val Rust = Color(0xFF9C512C)
val Muted = Color(0xFF65716A)

@Composable
fun DiaryTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme=lightColorScheme(primary=Pine, onPrimary=Color.White,
        secondary=Rust, background=Paper, surface=Paper, surfaceVariant=Color(0xFFECEEE5),
        onSurface=Ink, onBackground=Ink, outline=Color(0xFF7C8780)),
        typography=Typography(
            headlineLarge=TextStyle(fontSize=30.sp, lineHeight=38.sp, fontWeight=FontWeight.SemiBold),
            titleLarge=TextStyle(fontSize=22.sp, lineHeight=30.sp, fontWeight=FontWeight.Medium),
            bodyLarge=TextStyle(fontSize=17.sp, lineHeight=29.sp),
            bodyMedium=TextStyle(fontSize=15.sp, lineHeight=24.sp),
            labelLarge=TextStyle(fontSize=14.sp, fontWeight=FontWeight.Medium)
        ), content=content)
}
