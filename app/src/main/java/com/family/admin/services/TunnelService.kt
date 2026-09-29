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
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.util.regex.Pattern

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v5.0 — Cloudflare Tunnel Service
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Runs cloudflared as a child process to expose the local Flask
 *  server (127.0.0.1:5000) to the public internet via Cloudflare
 *  Quick Tunnel (*.trycloudflare.com).
 *
 *  After finding the URL, syncs it to Python tunnel_manager so
 *  that HTTP endpoints reflect the live state.
 *
 *  Lifecycle:
 *    onCreate   → locate binary, start process, parse URL
 *    onDestroy  → destroy process, clear state, sync "" to Python
 * ═══════════════════════════════════════════════════════════════
 */
class TunnelService : Service() {

    companion object {
        private const val TAG = "TunnelService"
        private const val CHANNEL_ID = "family_tunnel"
        private const val NOTIFICATION_ID = 9002
        private const val LOCAL_SERVER = "http://127.0.0.1:5000"

        // URL regex: https://xxx-yyy-zzz.trycloudflare.com
        private val URL_PATTERN: Pattern =
            Pattern.compile("https://[a-z0-9-]+\\.trycloudflare\\.com")

        @Volatile
        var isRunning: Boolean = false
            private set

        @Volatile
        var publicUrl: String? = null
            private set

        @Volatile
        var lastError: String? = null
            private set

        fun start(context: Context) {
            val intent = Intent(context, TunnelService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, TunnelService::class.java))
        }
    }

    private var process: Process? = null
    private var readerThread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        startForeground(NOTIFICATION_ID, buildNotification("Starting tunnel…"))
        readerThread = Thread { runTunnel() }.also { it.start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy")
        try {
            process?.destroy()
            process?.waitFor()
        } catch (t: Throwable) {
            Log.w(TAG, "destroy failed: ${t.message}")
        }
        process = null
        isRunning = false
        publicUrl = null
        // Notify Python that tunnel is gone
        syncUrlToPython("")
        super.onDestroy()
    }

    // ─────────────────────────────────────────────────────────
    //  Tunnel main
    // ─────────────────────────────────────────────────────────

    private fun runTunnel() {
        try {
            val binary = resolveBinary()
            if (binary == null) {
                lastError = "cloudflared binary not found"
                isRunning = false
                updateNotification("Tunnel failed: binary not found")
                Log.e(TAG, "Binary not found in nativeLibraryDir")
                return
            }

            // Verify executable
            if (!binary.canExecute()) {
                try {
                    binary.setExecutable(true)
                } catch (t: Throwable) {
                    Log.w(TAG, "setExecutable failed: ${t.message}")
                }
            }

            Log.i(TAG, "Binary: ${binary.absolutePath}")
            Log.i(TAG, "Binary size: ${binary.length()} bytes")

            val cmd = listOf(
                binary.absolutePath,
                "tunnel",
                "--url", LOCAL_SERVER,
                "--protocol", "http2",
                "--no-autoupdate"
            )

            Log.i(TAG, "Exec: ${cmd.joinToString(" ")}")

            val pb = ProcessBuilder(cmd)
                .redirectErrorStream(true)

            process = pb.start()
            isRunning = true
            updateNotification("Tunnel starting…")

            // Read stdout line by line, look for URL
            val reader = BufferedReader(InputStreamReader(process!!.inputStream))
            var line: String?
            while (reader.readLine().also { line = it } != null) {
                val l = line ?: continue
                Log.d(TAG, "[cf] $l")
                if (publicUrl == null) {
                    val m = URL_PATTERN.matcher(l)
                    if (m.find()) {
                        publicUrl = m.group(0)
                        lastError = null
                        Log.i(TAG, "Public URL: $publicUrl")
                        updateNotification("Tunnel: $publicUrl")
                        // Sync URL to Python tunnel_manager
                        syncUrlToPython(publicUrl)
                    }
                }
            }

            // Process ended
            val exit = try { process?.exitValue() } catch (_: Throwable) { -1 }
            Log.w(TAG, "cloudflared exited with $exit")
            isRunning = false
            publicUrl = null
            syncUrlToPython("")
            updateNotification("Tunnel stopped (exit=$exit)")
        } catch (t: Throwable) {
            Log.e(TAG, "runTunnel failed", t)
            lastError = t.message ?: t.javaClass.simpleName
            isRunning = false
            syncUrlToPython("")
            updateNotification("Tunnel error: $lastError")
        }
    }

    // ─────────────────────────────────────────────────────────
    //  Binary resolution
    // ─────────────────────────────────────────────────────────

    private fun resolveBinary(): File? {
        val nativeDir = applicationInfo.nativeLibraryDir
        Log.i(TAG, "nativeLibraryDir = $nativeDir")

        // Primary: libcloudflared.so extracted to nativeLibraryDir
        val path = File(nativeDir, "libcloudflared.so")
        if (path.exists()) {
            Log.i(TAG, "Found libcloudflared.so (${path.length()} bytes)")
            return path
        }

        // Fallback: some builds drop the "lib" prefix
        val alt = File(nativeDir, "cloudflared")
        if (alt.exists()) {
            Log.i(TAG, "Found cloudflared (${alt.length()} bytes)")
            return alt
        }

        // Last resort: list what's there for debugging
        try {
            val dir = File(nativeDir)
            val files = dir.listFiles()
            if (files != null) {
                Log.e(TAG, "nativeLibraryDir contents:")
                for (f in files) {
                    Log.e(TAG, "  ${f.name} (${f.length()} bytes)")
                }
            }
        } catch (t: Throwable) {
            Log.e(TAG, "listFiles failed: ${t.message}")
        }

        return null
    }

    // ─────────────────────────────────────────────────────────
    //  Sync URL to Python
    // ─────────────────────────────────────────────────────────

    private fun syncUrlToPython(url: String?) {
        try {
            if (!Python.isStarted()) {
                Log.w(TAG, "Python not started, skip sync")
                return
            }
            val py = Python.getInstance()
            val mod = py.getModule("tunnel_manager")
            mod.callAttr("set_external_url", url ?: "")
            Log.i(TAG, "Python synced: url='$url'")
        } catch (t: Throwable) {
            Log.w(TAG, "Python sync failed: ${t.message}")
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
        val pi = PendingIntent.getActivity(this, 1, intent, pendingFlags)

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Family Admin — Tunnel")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
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
