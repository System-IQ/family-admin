package com.family.admin

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.StrictMode
import android.util.Log
import androidx.appcompat.app.AppCompatDelegate
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.family.admin.services.TunnelService
import com.google.firebase.FirebaseApp
import timber.log.Timber
import java.io.PrintWriter
import java.io.StringWriter

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v5.0 — Application Class
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Initialization order (strict):
 *    1. Timber logging
 *    2. Crash handler
 *    3. StrictMode (debug only)
 *    4. Theme
 *    5. Notification channels
 *    6. Firebase
 *    7. Chaquopy (Python runtime)
 *    8. Tunnel bridge (Python ↔ Kotlin)
 * ═══════════════════════════════════════════════════════════════
 */
class FamilyAdminApp : Application() {

    companion object {
        private const val TAG = "FamilyAdminApp"

        const val CHANNEL_SERVER = "family_server"
        const val CHANNEL_ALERTS = "family_alerts"
        const val CHANNEL_DEVICES = "family_devices"
        const val CHANNEL_EMERGENCY = "family_emergency"

        const val PREFS_USER = "user_prefs"
        const val PREFS_SETTINGS = "settings_prefs"
        const val PREFS_THEME = "theme_prefs"

        @Volatile
        private var instance: FamilyAdminApp? = null

        fun getInstance(): FamilyAdminApp? = instance

        fun getContext(): Context = instance?.applicationContext
            ?: throw IllegalStateException("App not initialized")
    }

    private var pythonReady = false
    private var firebaseReady = false
    private var channelsReady = false
    private var tunnelBridgeReady = false

    override fun onCreate() {
        super.onCreate()
        instance = this

        Log.i(TAG, "═══ Family Admin v5.0 starting ═══")

        setupTimber()
        setupCrashHandler()
        setupStrictMode()
        setupTheme()
        createNotificationChannels()
        initializeFirebase()
        initializeChaquopy()

        Log.i(TAG, "✅ FamilyAdminApp initialized")
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 1: Timber
    // ═══════════════════════════════════════════════════════════

    private fun setupTimber() {
        try {
            if (BuildConfig.DEBUG) {
                Timber.plant(Timber.DebugTree())
            } else {
                Timber.plant(object : Timber.Tree() {
                    override fun log(priority: Int, tag: String?, message: String, t: Throwable?) {
                        if (priority >= Log.WARN) {
                            Log.println(priority, tag ?: TAG, message)
                        }
                    }
                })
            }
        } catch (e: Exception) {
            Log.e(TAG, "Timber setup failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 2: Crash handler
    // ═══════════════════════════════════════════════════════════

    private fun setupCrashHandler() {
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val sw = StringWriter()
                throwable.printStackTrace(PrintWriter(sw))
                Log.e(TAG, "CRASH: ${throwable.message}")
                Log.e(TAG, sw.toString())
                saveCrashLog(sw.toString())
            } catch (_: Exception) {
            } finally {
                defaultHandler?.uncaughtException(thread, throwable)
            }
        }
    }

    private fun saveCrashLog(stackTrace: String) {
        try {
            val file = java.io.File(filesDir, "logs/crash.log")
            file.parentFile?.mkdirs()
            file.appendText("\n${System.currentTimeMillis()}\n$stackTrace\n---\n")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save crash log", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 3: StrictMode
    // ═══════════════════════════════════════════════════════════

    private fun setupStrictMode() {
        if (!BuildConfig.DEBUG) return
        try {
            StrictMode.setThreadPolicy(
                StrictMode.ThreadPolicy.Builder()
                    .detectDiskReads()
                    .detectDiskWrites()
                    .detectNetwork()
                    .penaltyLog()
                    .build()
            )
        } catch (e: Exception) {
            Log.e(TAG, "StrictMode failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 4: Theme
    // ═══════════════════════════════════════════════════════════

    private fun setupTheme() {
        try {
            val prefs = getSharedPreferences(PREFS_THEME, MODE_PRIVATE)
            val mode = prefs.getInt("theme_mode", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
            AppCompatDelegate.setDefaultNightMode(mode)
        } catch (e: Exception) {
            Log.e(TAG, "Theme setup failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 5: Notification channels
    // ═══════════════════════════════════════════════════════════

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            channelsReady = true
            return
        }
        try {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

            // Server (low)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_SERVER,
                    getString(R.string.channel_server_name),
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = getString(R.string.channel_server_desc)
                    setShowBadge(false)
                }
            )

            // Alerts (high)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ALERTS,
                    getString(R.string.channel_alerts_name),
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = getString(R.string.channel_alerts_desc)
                    enableVibration(true)
                    setSound(
                        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                            .build()
                    )
                }
            )

            // Devices (default)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_DEVICES,
                    getString(R.string.channel_devices_name),
                    NotificationManager.IMPORTANCE_DEFAULT
                ).apply {
                    description = getString(R.string.channel_devices_desc)
                }
            )

            // Emergency (high, bypass DND)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_EMERGENCY,
                    getString(R.string.channel_emergency_name),
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = getString(R.string.channel_emergency_desc)
                    setBypassDnd(true)
                    enableVibration(true)
                    setSound(
                        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ALARM)
                            .build()
                    )
                }
            )

            channelsReady = true
            Timber.i("✅ 4 notification channels created")
        } catch (e: Exception) {
            Log.e(TAG, "Channels failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 6: Firebase
    // ═══════════════════════════════════════════════════════════

    private fun initializeFirebase() {
        try {
            FirebaseApp.initializeApp(this)
            firebaseReady = true
            Timber.i("✅ Firebase initialized")
        } catch (e: Exception) {
            Log.e(TAG, "Firebase init failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 7: Chaquopy
    // ═══════════════════════════════════════════════════════════

    private fun initializeChaquopy() {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
                Timber.i("✅ Python runtime started")
            }
            pythonReady = true

            val py = Python.getInstance()
            val sys = py.getModule("sys")
            val version = sys.callAttr("get", "version").toString()
            Timber.i("Python version: ${version.take(50)}")

            // Now that Python is ready, register the tunnel bridge
            registerTunnelBridge()
        } catch (e: Exception) {
            Log.e(TAG, "Chaquopy init failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Step 8: Tunnel bridge (Python ↔ Kotlin)
    // ═══════════════════════════════════════════════════════════

    private fun registerTunnelBridge() {
        try {
            val py = Python.getInstance()
            val tunnelModule = py.getModule("tunnel_manager")

            // Create the bridge object
            val bridge = TunnelBridge(this)

            // Register: tunnel_manager.register_provider(
            //     "cloudflare", bridge, bridge, bridge
            // )
            // Chaquopy will auto-wrap the Kotlin object as a PyObject.
            // Python will call bridge.start(), bridge.stop(), bridge.status()
            tunnelModule.callAttr(
                "register_provider",
                "cloudflare",
                bridge,   // start_cb  → bridge.start()
                bridge,   // stop_cb   → bridge.stop()
                bridge,   // status_cb → bridge.status()
                null,     // health_cb → None
                10        // priority
            )

            tunnelBridgeReady = true
            Timber.i("✅ Tunnel bridge registered")
        } catch (e: Exception) {
            Log.e(TAG, "Tunnel bridge registration failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Public API
    // ═══════════════════════════════════════════════════════════

    fun isPythonReady(): Boolean = pythonReady
    fun isFirebaseReady(): Boolean = firebaseReady
    fun isChannelsReady(): Boolean = channelsReady
    fun isTunnelBridgeReady(): Boolean = tunnelBridgeReady

    fun getAppInfo(): Map<String, Any> = mapOf(
        "name" to BuildConfig.APPLICATION_ID,
        "version" to BuildConfig.VERSION_NAME,
        "versionCode" to BuildConfig.VERSION_CODE,
        "sdk" to Build.VERSION.SDK_INT,
        "pythonReady" to pythonReady,
        "firebaseReady" to firebaseReady,
        "channelsReady" to channelsReady,
        "tunnelBridgeReady" to tunnelBridgeReady
    )

    override fun onLowMemory() {
        super.onLowMemory()
        Timber.w("⚠️ Low memory")
    }

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        Timber.d("onTrimMemory: $level")
    }
}

/**
 * ═══════════════════════════════════════════════════════════════
 *  TunnelBridge — Python-callable adapter for TunnelService
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Python calls:
 *    url = bridge.start()       → starts TunnelService, returns current URL (may be "")
 *    ok  = bridge.stop()        → stops TunnelService, returns True
 *    st  = bridge.status()      → dict {running, url}
 *
 *  Note: start() does NOT block waiting for URL. Python must
 *  poll status() until url is non-empty.
 * ═══════════════════════════════════════════════════════════════
 */
class TunnelBridge(private val context: Context) {

    /** Called by Python: url = bridge.start() */
    fun start(): String {
        Log.i("TunnelBridge", "Python → start()")
        try {
            TunnelService.start(context)
        } catch (t: Throwable) {
            Log.e("TunnelBridge", "start() failed", t)
            return ""
        }
        // Return immediately — URL comes later via status()
        return TunnelService.publicUrl ?: ""
    }

    /** Called by Python: ok = bridge.stop() */
    fun stop(): Boolean {
        Log.i("TunnelBridge", "Python → stop()")
        return try {
            TunnelService.stop(context)
            true
        } catch (t: Throwable) {
            Log.e("TunnelBridge", "stop() failed", t)
            false
        }
    }

    /** Called by Python: st = bridge.status() */
    fun status(): Map<String, Any> {
        return mapOf(
            "running" to TunnelService.isRunning,
            "url" to (TunnelService.publicUrl ?: ""),
            "error" to (TunnelService.lastError ?: "")
        )
    }
}
