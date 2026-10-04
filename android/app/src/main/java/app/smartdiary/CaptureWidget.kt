package app.smartdiary

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

class CaptureWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        for (id in ids) {
            val views = RemoteViews(context.packageName, R.layout.capture_widget)
            for ((view, action) in listOf(R.id.voice to "app.smartdiary.RECORD", R.id.write to "app.smartdiary.WRITE")) {
                views.setOnClickPendingIntent(view, PendingIntent.getActivity(context, view,
                    Intent(context, MainActivity::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            }
            manager.updateAppWidget(id, views)
        }
    }
}
