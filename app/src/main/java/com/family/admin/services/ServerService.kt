package com.family.admin.services

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.family.admin.MainActivity
import com.family.admin.R

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v5.0 — Server Service
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Runs the Python Flask server as a Foreground Service.
 *  After Python is up, auto-starts TunnelService.
 * ═══════════════════════════════════════════════════════════════
 */
class ServerService : Service() {

    companion object {
        private const val TAG = "ServerService"
        private const val CHANNEL_ID = "family_server"
        private const val NOTIFICATION_ID = 9001

        @Volatile
        var isRunning: Boolean = false
            private set

        @Volatile
        var lastResult: String = "not_started"
            private set

        @Volatile
        var startupError: String? = null
            private set

        fun start(context: Context) {
            val intent = Intent(context, ServerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ServerService::class.java))
        }
    }

    private var workerThread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        startForeground(NOTIFICATION_ID, buildNotification("Starting…"))

        workerThread = Thread {
            try {
                startPythonServer()
                // After Python is up, wait a bit and start the Tunnel service
                try {
                    Thread.sleep(2000)
                    Log.i(TAG, "Auto-starting TunnelService from ServerService")
                    TunnelService.start(this)
                } catch (t: Throwable) {
                    Log.w(TAG, "Tunnel auto-start failed: ${t.message}")
                }
            } catch (t: Throwable) {
                Log.e(TAG, "Python start failed", t)
                startupError = t.message ?: t.javaClass.simpleName
                lastResult = "exception"
                isRunning = false
                updateNotification("Server failed: ${startupError}")
            }
        }.also { it.start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i(TAG, "onStartCommand")
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy")
        try {
            val py = Python.getInstance()
            val serverModule = py.getModule("server")
            serverModule.callAttr("stop_server")
        } catch (t: Throwable) {
            Log.w(TAG, "stop_server failed: ${t.message}")
        }
        isRunning = false
        lastResult = "stopped"
        super.onDestroy()
    }

    // ─────────────────────────────────────────────────────────
    //  Python bootstrap
    // ─────────────────────────────────────────────────────────

    private fun startPythonServer() {
        Log.i(TAG, "Checking Python runtime…")
        if (!Python.isStarted()) {
            Log.w(TAG, "Python not started — cannot launch server")
            lastResult = "python_not_started"
            startupError = "Python runtime not initialized"
            updateNotification("Python not initialized")
            return
        }

        val py = Python.getInstance()
        val serverModule = py.getModule("server")

        Log.i(TAG, "Calling server.start_server()…")
        updateNotification("Starting Python server…")

        val result = try {
            serverModule.callAttr("start_server").toString()
        } catch (t: Throwable) {
            Log.e(TAG, "start_server raised", t)
            startupError = t.message ?: t.javaClass.simpleName
            "exception"
        }

        lastResult = result
        Log.i(TAG, "start_server returned: $result")

        when (result) {
            "started" -> {
                isRunning = true
                startupError = null
                updateNotification("Server running on port 5000")
            }
            "already_running" -> {
                isRunning = true
                updateNotification("Server already running")
            }
            else -> {
                isRunning = false
                startupError = result
                updateNotification("Server failed: $result")
            }
        }
    }

    // ─────────────────────────────────────────────────────────
    //  Notification
    // ─────────────────────────────────────────────────────────

    private fun buildNotification(text: String): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingFlags =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            else PendingIntent.FLAG_UPDATE_CURRENT
        val pi = PendingIntent.getActivity(this, 0, intent, pendingFlags)

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Family Admin — Server")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
    }

    private fun updateNotification(text: String) {
        try {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(NOTIFICATION_ID, buildNotification(text))
        } catch (t: Throwable) {
            Log.w(TAG, "updateNotification failed: ${t.message}")
        }
    }
}
