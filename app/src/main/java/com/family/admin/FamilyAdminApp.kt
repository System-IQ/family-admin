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
import androidx.annotation.RequiresApi
import androidx.appcompat.app.AppCompatDelegate
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.google.firebase.FirebaseApp
import timber.log.Timber
import java.io.PrintWriter
import java.io.StringWriter

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v3.0 — Application Class
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Responsibilities:
 *  ─────────────────────────────────────────────────────────────
 *  1. Initialize Timber logging
 *  2. Create notification channels
 *  3. Initialize Firebase
 *  4. Initialize Chaquopy (Python runtime)
 *  5. Set up global exception handler
 *  6. Configure StrictMode (debug only)
 *  7. Set up AppCompatDelegate
 *
 *  Lifecycle:
 *  ─────────────────────────────────────────────────────────────
 *  onCreate()  → Called when app process starts
 *  onTerminate() → Called in emulator only (not real devices)
 *
 *  ═══════════════════════════════════════════════════════════════
 */
class FamilyAdminApp : Application() {

    companion object {
        private const val TAG = "FamilyAdminApp"

        // ═══ Notification Channels ═══
        const val CHANNEL_SERVER = "family_server"
        const val CHANNEL_ALERTS = "family_alerts"
        const val CHANNEL_DEVICES = "family_devices"
        const val CHANNEL_EMERGENCY = "family_emergency"

        // ═══ SharedPreferences ═══
        const val PREFS_USER = "user_prefs"
        const val PREFS_SETTINGS = "settings_prefs"
        const val PREFS_THEME = "theme_prefs"

        // ═══ Singleton ═══
        @Volatile
        private var instance: FamilyAdminApp? = null

        fun getInstance(): FamilyAdminApp? = instance

        fun getContext(): Context = instance?.applicationContext
            ?: throw IllegalStateException("App not initialized")
    }

    // ═══ State ═══
    private var pythonReady = false
    private var firebaseReady = false
    private var channelsReady = false

    /**
     * ═══════════════════════════════════════════════════════════
     *  onCreate — Application Entry Point
     * ═══════════════════════════════════════════════════════════
     */
    override fun onCreate() {
        super.onCreate()
        instance = this

        Log.i(TAG, "╔════════════════════════════════════════════╗")
        Log.i(TAG, "║  Family Admin v3.0 — Starting...          ║")
        Log.i(TAG, "╚════════════════════════════════════════════╝")

        // ═══ Step 1: Setup Timber (logging) ═══
        setupTimber()

        // ═══ Step 2: Setup Crash Handler ═══
        setupCrashHandler()

        // ═══ Step 3: Setup StrictMode (debug only) ═══
        setupStrictMode()

        // ═══ Step 4: Setup Theme ═══
        setupTheme()

        // ═══ Step 5: Create Notification Channels ═══
        createNotificationChannels()

        // ═══ Step 6: Initialize Firebase ═══
        initializeFirebase()

        // ═══ Step 7: Initialize Chaquopy (Python) ═══
        initializeChaquopy()

        // ═══ Done ═══
        Log.i(TAG, "✅ FamilyAdminApp initialized successfully")
        Timber.i("App version: %s | SDK: %d", BuildConfig.VERSION_NAME, Build.VERSION.SDK_INT)
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 1: Timber Logging
     * ═══════════════════════════════════════════════════════════
     */
    private fun setupTimber() {
        try {
            if (BuildConfig.DEBUG) {
                // Debug: log everything with tree
                Timber.plant(Timber.DebugTree())
                Timber.d("Timber planted (DEBUG mode)")
            } else {
                // Release: only warnings and errors
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

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 2: Global Crash Handler
     * ═══════════════════════════════════════════════════════════
     */
    private fun setupCrashHandler() {
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()

        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                // Format stack trace
                val sw = StringWriter()
                throwable.printStackTrace(PrintWriter(sw))
                val stackTrace = sw.toString()

                // Log it
                Log.e(TAG, "╔════════════════════════════════════════════╗")
                Log.e(TAG, "║  🔥 CRASH DETECTED                          ║")
                Log.e(TAG, "╚════════════════════════════════════════════╝")
                Log.e(TAG, "Thread: ${thread.name}")
                Log.e(TAG, "Error: ${throwable.message}")
                Log.e(TAG, stackTrace)

                // Save to file for later analysis
                saveCrashLog(stackTrace)

            } catch (e: Exception) {
                Log.e(TAG, "Error in crash handler", e)
            } finally {
                // Call default handler
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

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 3: StrictMode (Debug Only)
     * ═══════════════════════════════════════════════════════════
     */
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

            StrictMode.setVmPolicy(
                StrictMode.VmPolicy.Builder()
                    .detectLeakedSqlLiteObjects()
                    .detectLeakedClosableObjects()
                    .penaltyLog()
                    .build()
            )

            Timber.d("StrictMode enabled (DEBUG)")
        } catch (e: Exception) {
            Log.e(TAG, "StrictMode setup failed", e)
        }
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 4: Theme
     * ═══════════════════════════════════════════════════════════
     */
    private fun setupTheme() {
        try {
            val prefs = getSharedPreferences(PREFS_THEME, MODE_PRIVATE)
            val mode = prefs.getInt("theme_mode", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)

            AppCompatDelegate.setDefaultNightMode(mode)

            Timber.d("Theme mode set to: %d", mode)
        } catch (e: Exception) {
            Log.e(TAG, "Theme setup failed", e)
        }
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 5: Notification Channels
     * ═══════════════════════════════════════════════════════════
     */
    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            channelsReady = true
            return
        }

        try {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

            // ═══ Server Channel (Low priority, persistent) ═══
            val serverChannel = NotificationChannel(
                CHANNEL_SERVER,
                getString(R.string.channel_server_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.channel_server_desc)
                setShowBadge(false)
                enableLights(false)
                enableVibration(false)
            }
            nm.createNotificationChannel(serverChannel)

            // ═══ Alerts Channel (High priority) ═══
            val alertsChannel = NotificationChannel(
                CHANNEL_ALERTS,
                getString(R.string.channel_alerts_name),
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = getString(R.string.channel_alerts_desc)
                setShowBadge(true)
                enableLights(true)
                lightColor = getColor(R.color.primary)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 250, 250, 250)
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                    AudioAttributes.Builder()
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                        .build()
                )
            }
            nm.createNotificationChannel(alertsChannel)

            // ═══ Devices Channel (Default priority) ═══
            val devicesChannel = NotificationChannel(
                CHANNEL_DEVICES,
                getString(R.string.channel_devices_name),
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = getString(R.string.channel_devices_desc)
                setShowBadge(true)
                enableLights(false)
                enableVibration(true)
            }
            nm.createNotificationChannel(devicesChannel)

            // ═══ Emergency Channel (Max priority) ═══
            val emergencyChannel = NotificationChannel(
                CHANNEL_EMERGENCY,
                getString(R.string.channel_emergency_name),
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = getString(R.string.channel_emergency_desc)
                setShowBadge(true)
                enableLights(true)
                lightColor = getColor(R.color.alert_critical)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 500, 250, 500, 250, 500)
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                    AudioAttributes.Builder()
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .build()
                )
                setBypassDnd(true)
            }
            nm.createNotificationChannel(emergencyChannel)

            channelsReady = true
            Timber.i("✅ 4 notification channels created")

        } catch (e: Exception) {
            Log.e(TAG, "Notification channels failed", e)
        }
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 6: Firebase
     * ═══════════════════════════════════════════════════════════
     */
    private fun initializeFirebase() {
        try {
            FirebaseApp.initializeApp(this)
            firebaseReady = true
            Timber.i("✅ Firebase initialized")
        } catch (e: Exception) {
            Log.e(TAG, "Firebase init failed", e)
        }
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Step 7: Chaquopy (Python Runtime)
     * ═══════════════════════════════════════════════════════════
     */
    private fun initializeChaquopy() {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
                Timber.i("✅ Python runtime started")
            } else {
                Timber.d("Python already running")
            }
            pythonReady = true

            // ═══ Test Python ═══
            val py = Python.getInstance()
            val sys = py.getModule("sys")
            val version = sys.callAttr("get", "version").toString()
            Timber.i("Python version: %s", version.take(50))

        } catch (e: Exception) {
            Log.e(TAG, "Chaquopy init failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Public API
    // ═══════════════════════════════════════════════════════════

    fun isPythonReady(): Boolean = pythonReady
    fun isFirebaseReady(): Boolean = firebaseReady
    fun isChannelsReady(): Boolean = channelsReady

    fun getAppInfo(): Map<String, Any> = mapOf(
        "name" to BuildConfig.APPLICATION_ID,
        "version" to BuildConfig.VERSION_NAME,
        "versionCode" to BuildConfig.VERSION_CODE,
        "sdk" to Build.VERSION.SDK_INT,
        "pythonReady" to pythonReady,
        "firebaseReady" to firebaseReady,
        "channelsReady" to channelsReady
    )

    // ═══════════════════════════════════════════════════════════
    //  Lifecycle
    // ═══════════════════════════════════════════════════════════

    override fun onTerminate() {
        super.onTerminate()
        Log.i(TAG, "App terminating")
    }

    override fun onLowMemory() {
        super.onLowMemory()
        Timber.w("⚠️ Low memory warning")
    }

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        Timber.d("onTrimMemory: %d", level)
    }
}
