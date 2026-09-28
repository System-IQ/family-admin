package com.family.admin.services

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.RingtoneManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.family.admin.FamilyAdminApp
import com.family.admin.MainActivity
import com.family.admin.R
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import timber.log.Timber

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v3.0 — FCM Service
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Receives Firebase Cloud Messaging messages from:
 *   • Family Client devices (locations, alerts)
 *   • Server notifications
 *   • System messages
 *
 *  Message types:
 *   • LOCATION_UPDATE
 *   • DEVICE_ALERT
 *   • GPS_STATUS
 *   • BATTERY_LOW
 *   • DEVICE_OFFLINE
 *   • EMERGENCY
 *
 *  ═══════════════════════════════════════════════════════════════
 */
class FcmService : FirebaseMessagingService() {

    companion object {
        private const val TAG = "FcmService"

        // ═══ Notification IDs ═══
        private const val NOTIF_ID_ALERT = 1000
        private const val NOTIF_ID_DEVICE = 2000
        private const val NOTIF_ID_EMERGENCY = 3000

        // ═══ Message Types ═══
        const val TYPE_LOCATION = "LOCATION_UPDATE"
        const val TYPE_ALERT = "DEVICE_ALERT"
        const val TYPE_GPS = "GPS_STATUS"
        const val TYPE_BATTERY = "BATTERY_LOW"
        const val TYPE_OFFLINE = "DEVICE_OFFLINE"
        const val TYPE_EMERGENCY = "EMERGENCY"
    }

    // ═══════════════════════════════════════════════════════════════
    //  onNewToken — Called when FCM token is refreshed
    // ═══════════════════════════════════════════════════════════════

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Timber.i("New FCM token received")

        try {
            // ═══ Save token locally ═══
            val prefs = getSharedPreferences("fcm_token", Context.MODE_PRIVATE)
            prefs.edit().putString("current_token", token).apply()

            // ═══ TODO: Send token to server (Stage 3) ═══
            // ServerApi.updateToken(token)

        } catch (e: Exception) {
            Log.e(TAG, "onNewToken failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  onMessageReceived — Called when message arrives
    // ═══════════════════════════════════════════════════════════════

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)

        try {
            Timber.d("FCM message received")

            // ═══ Extract data ═══
            val data = message.data
            val messageType = data["type"] ?: message.notification?.title ?: "UNKNOWN"
            val title = data["title"] ?: message.notification?.title ?: "Family Admin"
            val body = data["body"] ?: message.notification?.body ?: ""
            val deviceId = data["device_id"] ?: ""
            val priority = data["priority"] ?: "normal"

            Timber.i("Type: $messageType | Device: $deviceId")

            // ═══ Route by type ═══
            when (messageType) {
                TYPE_LOCATION -> handleLocationUpdate(data, title, body, deviceId)
                TYPE_ALERT -> handleDeviceAlert(data, title, body, deviceId)
                TYPE_GPS -> handleGpsStatus(data, title, body, deviceId)
                TYPE_BATTERY -> handleBatteryLow(data, title, body, deviceId)
                TYPE_OFFLINE -> handleDeviceOffline(data, title, body, deviceId)
                TYPE_EMERGENCY -> handleEmergency(data, title, body, deviceId)
                else -> handleGeneric(data, title, body, priority)
            }

        } catch (e: Exception) {
            Log.e(TAG, "onMessageReceived failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Handlers
    // ═══════════════════════════════════════════════════════════════

    private fun handleLocationUpdate(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            val lat = data["lat"]?.toDoubleOrNull()
            val lon = data["lon"]?.toDoubleOrNull()
            val speed = data["speed"]?.toDoubleOrNull() ?: 0.0

            val text = if (lat != null && lon != null) {
                val speedKmh = speed * 3.6
                "📍 $title\n$body\nالسرعة: ${"%.1f".format(speedKmh)} كم/س"
            } else {
                "📍 $title\n$body"
            }

            showNotification(
                channelId = FamilyAdminApp.CHANNEL_DEVICES,
                notificationId = NOTIF_ID_DEVICE,
                title = title,
                body = text,
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_DEFAULT
            )

            Timber.d("Location update displayed")
        } catch (e: Exception) {
            Log.e(TAG, "handleLocationUpdate failed", e)
        }
    }

    private fun handleDeviceAlert(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            val alertType = data["alert_type"] ?: "general"

            val emoji = when (alertType) {
                "geofence_enter" -> "🏠"
                "geofence_exit" -> "🚪"
                "high_speed" -> "🚀"
                "sos" -> "🆘"
                else -> "⚠️"
            }

            showNotification(
                channelId = FamilyAdminApp.CHANNEL_ALERTS,
                notificationId = NOTIF_ID_ALERT + deviceId.hashCode(),
                title = "$emoji $title",
                body = body,
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_HIGH
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleDeviceAlert failed", e)
        }
    }

    private fun handleGpsStatus(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            val status = data["status"] ?: "unknown"
            val emoji = if (status == "on") "🟢" else "🔴"
            val message = if (status == "on") {
                "تم تشغيل GPS على جهاز $title"
            } else {
                "تم إطفاء GPS على جهاز $title"
            }

            showNotification(
                channelId = FamilyAdminApp.CHANNEL_ALERTS,
                notificationId = NOTIF_ID_ALERT + deviceId.hashCode(),
                title = "$emoji تغيير حالة GPS",
                body = message,
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_HIGH
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleGpsStatus failed", e)
        }
    }

    private fun handleBatteryLow(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            val level = data["level"] ?: "?"
            showNotification(
                channelId = FamilyAdminApp.CHANNEL_ALERTS,
                notificationId = NOTIF_ID_ALERT + deviceId.hashCode(),
                title = "🔋 بطارية منخفضة",
                body = "جهاز $title — ${level}%",
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_HIGH
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleBatteryLow failed", e)
        }
    }

    private fun handleDeviceOffline(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            val minutes = data["minutes"] ?: "?"
            showNotification(
                channelId = FamilyAdminApp.CHANNEL_ALERTS,
                notificationId = NOTIF_ID_ALERT + deviceId.hashCode(),
                title = "📴 جهاز غير متصل",
                body = "$title — منذ $minutes دقيقة",
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_DEFAULT
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleDeviceOffline failed", e)
        }
    }

    private fun handleEmergency(
        data: Map<String, String>,
        title: String,
        body: String,
        deviceId: String
    ) {
        try {
            showNotification(
                channelId = FamilyAdminApp.CHANNEL_EMERGENCY,
                notificationId = NOTIF_ID_EMERGENCY + deviceId.hashCode(),
                title = "🆘 طوارئ — $title",
                body = body.ifEmpty { "حالة طوارئ من $title" },
                deviceId = deviceId,
                priority = NotificationCompat.PRIORITY_MAX,
                isEmergency = true
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleEmergency failed", e)
        }
    }

    private fun handleGeneric(
        data: Map<String, String>,
        title: String,
        body: String,
        priority: String
    ) {
        try {
            val notifPriority = when (priority.lowercase()) {
                "high" -> NotificationCompat.PRIORITY_HIGH
                "low" -> NotificationCompat.PRIORITY_LOW
                "max" -> NotificationCompat.PRIORITY_MAX
                else -> NotificationCompat.PRIORITY_DEFAULT
            }

            showNotification(
                channelId = FamilyAdminApp.CHANNEL_DEVICES,
                notificationId = System.currentTimeMillis().toInt(),
                title = title,
                body = body,
                deviceId = data["device_id"] ?: "",
                priority = notifPriority
            )
        } catch (e: Exception) {
            Log.e(TAG, "handleGeneric failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Show Notification
    // ═══════════════════════════════════════════════════════════════

    private fun showNotification(
        channelId: String,
        notificationId: Int,
        title: String,
        body: String,
        deviceId: String,
        priority: Int,
        isEmergency: Boolean = false
    ) {
        try {
            // ═══ Intent to MainActivity ═══
            val intent = Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra("device_id", deviceId)
                putExtra("from_notification", true)
            }

            val pendingFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }

            val pendingIntent = PendingIntent.getActivity(
                this,
                notificationId,
                intent,
                pendingFlags
            )

            // ═══ Build notification ═══
            val builder = NotificationCompat.Builder(this, channelId)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setPriority(priority)
                .setAutoCancel(true)
                .setContentIntent(pendingIntent)
                .setColor(getColor(R.color.primary))
                .setWhen(System.currentTimeMillis())
                .setShowWhen(true)

            // ═══ Emergency: bypass DND + vibrate ═══
            if (isEmergency) {
                builder.setCategory(NotificationCompat.CATEGORY_ALARM)
                builder.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    builder.setAllowSystemGeneratedContextualActions(true)
                }
            }

            // ═══ Show notification ═══
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(notificationId, builder.build())

            Timber.d("Notification shown: $title")

        } catch (e: Exception) {
            Log.e(TAG, "showNotification failed", e)
        }
    }
}
