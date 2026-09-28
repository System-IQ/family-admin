package com.family.admin.ui

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.animation.AnimationUtils
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.family.admin.FamilyAdminApp
import com.family.admin.R
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import timber.log.Timber

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v3.0 — Splash Activity
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Responsibilities:
 *  ─────────────────────────────────────────────────────────────
 *  1. Show branded splash screen
 *  2. Initialize app components in background
 *  3. Fetch FCM token
 *  4. Check for updates
 *  5. Navigate to MainActivity when ready
 *
 *  Timeline:
 *  ─────────────────────────────────────────────────────────────
 *  0.0s → Show splash
 *  0.5s → Start initialization tasks
 *  1.5s → Animation ends
 *  2.0s → Navigate to MainActivity
 *
 *  ═══════════════════════════════════════════════════════════════
 */
class SplashActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "SplashActivity"

        // ═══ Minimum splash duration (for branding) ═══
        private const val MIN_SPLASH_DURATION = 1500L

        // ═══ Maximum wait time (in case of errors) ═══
        private const val MAX_WAIT_TIME = 5000L

        // ═══ FCM Token preference key ═══
        private const val PREFS_FCM = "fcm_token"
        private const val KEY_FCM_TOKEN = "current_token"
    }

    // ═══ Views ═══
    private lateinit var ivLogo: ImageView
    private lateinit var tvAppName: TextView
    private lateinit var tvVersion: TextView
    private lateinit var tvStatus: TextView

    // ═══ Coroutines ═══
    private val scope = CoroutineScope(Dispatchers.Main + Job())

    // ═══ State ═══
    private var isReady = false
    private var startTime = 0L

    // ═══════════════════════════════════════════════════════════════
    //  Lifecycle
    // ═══════════════════════════════════════════════════════════════

    override fun onCreate(savedInstanceState: Bundle?) {
        // ═══ Install Splash Screen (Android 12+) ═══
        val splashScreen = installSplashScreen()

        // ═══ Keep splash visible until ready ═══
        splashScreen.setKeepOnScreenCondition {
            !isReady
        }

        super.onCreate(savedInstanceState)

        // ═══ Record start time ═══
        startTime = System.currentTimeMillis()

        Log.i(TAG, "╔════════════════════════════════════════════╗")
        Log.i(TAG, "║  Splash Screen — Family Admin v3.0        ║")
        Log.i(TAG, "╚════════════════════════════════════════════╝")

        // ═══ Set content view ═══
        setContentView(R.layout.activity_splash)

        // ═══ Bind views ═══
        bindViews()

        // ═══ Apply entrance animations ═══
        applyEntranceAnimations()

        // ═══ Start initialization ═══
        initializeApp()
    }

    // ═══════════════════════════════════════════════════════════════
    //  Bind Views
    // ═══════════════════════════════════════════════════════════════

    private fun bindViews() {
        try {
            ivLogo = findViewById(R.id.ivLogo)
            tvAppName = findViewById(R.id.tvAppName)
            tvVersion = findViewById(R.id.tvVersion)
            tvStatus = findViewById(R.id.tvStatus)

            // ═══ Set version ═══
            tvVersion.text = getString(R.string.app_version)
            tvStatus.text = getString(R.string.server_starting)

        } catch (e: Exception) {
            Log.e(TAG, "bindViews failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Entrance Animations
    // ═══════════════════════════════════════════════════════════════

    private fun applyEntranceAnimations() {
        try {
            // ═══ Logo: fade in + scale up ═══
            val logoAnim = AnimationUtils.loadAnimation(this, android.R.anim.fade_in)
            logoAnim.duration = 800L
            ivLogo.startAnimation(logoAnim)

            // ═══ App Name: fade in (delayed) ═══
            val nameAnim = AnimationUtils.loadAnimation(this, android.R.anim.fade_in)
            nameAnim.duration = 800L
            nameAnim.startOffset = 200L
            tvAppName.startAnimation(nameAnim)

            // ═══ Version: fade in (delayed) ═══
            val versionAnim = AnimationUtils.loadAnimation(this, android.R.anim.fade_in)
            versionAnim.duration = 800L
            versionAnim.startOffset = 400L
            tvVersion.startAnimation(versionAnim)

            // ═══ Status: fade in (delayed) ═══
            val statusAnim = AnimationUtils.loadAnimation(this, android.R.anim.fade_in)
            statusAnim.duration = 800L
            statusAnim.startOffset = 600L
            tvStatus.startAnimation(statusAnim)

        } catch (e: Exception) {
            Log.e(TAG, "Animations failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Initialization
    // ═══════════════════════════════════════════════════════════════

    private fun initializeApp() {
        scope.launch {
            try {
                // ═══ Step 1: Wait minimum time (branding) ═══
                updateStatus("Loading...")
                delay(500)

                // ═══ Step 2: Check app state ═══
                updateStatus("Checking components...")
                checkAppComponents()

                // ═══ Step 3: Fetch FCM token ═══
                updateStatus("Connecting to server...")
                fetchFcmToken()

                // ═══ Step 4: Initialize Python server ═══
                updateStatus("Starting server...")
                startPythonServer()

                // ═══ Step 5: Wait for minimum duration ═══
                val elapsed = System.currentTimeMillis() - startTime
                val remaining = MIN_SPLASH_DURATION - elapsed
                if (remaining > 0) {
                    delay(remaining)
                }

                // ═══ Step 6: Ready! ═══
                updateStatus("Ready ✅")
                delay(300)

                isReady = true
                navigateToMain()

            } catch (e: Exception) {
                Log.e(TAG, "Initialization failed", e)
                updateStatus("Error: ${e.message}")
                delay(2000)
                // ═══ Navigate anyway (don't block user) ═══
                isReady = true
                navigateToMain()
            }
        }

        // ═══ Safety timeout ═══
        Handler(Looper.getMainLooper()).postDelayed({
            if (!isReady) {
                Log.w(TAG, "⚠️ Safety timeout reached")
                isReady = true
                navigateToMain()
            }
        }, MAX_WAIT_TIME)
    }

    // ═══════════════════════════════════════════════════════════════
    //  Check App Components
    // ═══════════════════════════════════════════════════════════════

    private suspend fun checkAppComponents() = withContext(Dispatchers.IO) {
        try {
            val app = FamilyAdminApp.getInstance()

            if (app == null) {
                Timber.w("App instance is null")
                return@withContext
            }

            // ═══ Check Firebase ═══
            if (app.isFirebaseReady()) {
                Timber.d("✅ Firebase ready")
            } else {
                Timber.w("⚠️ Firebase not ready")
            }

            // ═══ Check Channels ═══
            if (app.isChannelsReady()) {
                Timber.d("✅ Channels ready")
            } else {
                Timber.w("⚠️ Channels not ready")
            }

            // ═══ Check Python ═══
            if (app.isPythonReady()) {
                Timber.d("✅ Python ready")
            } else {
                Timber.w("⚠️ Python not ready")
            }

        } catch (e: Exception) {
            Log.e(TAG, "checkAppComponents failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Fetch FCM Token
    // ═══════════════════════════════════════════════════════════════

    private suspend fun fetchFcmToken() = withContext(Dispatchers.IO) {
        try {
            // ═══ Check if we already have a token ═══
            val prefs = getSharedPreferences(PREFS_FCM, MODE_PRIVATE)
            val existingToken = prefs.getString(KEY_FCM_TOKEN, null)

            if (!existingToken.isNullOrEmpty()) {
                Timber.d("FCM token already cached")
                return@withContext
            }

            // ═══ Fetch new token ═══
            FirebaseMessaging.getInstance().token
                .addOnCompleteListener { task ->
                    if (task.isSuccessful) {
                        val token = task.result
                        if (token != null) {
                            prefs.edit().putString(KEY_FCM_TOKEN, token).apply()
                            Timber.i("✅ FCM token: %s...", token.take(20))
                        }
                    } else {
                        Timber.w(task.exception, "Failed to get FCM token")
                    }
                }

            // ═══ Wait for token (max 3 seconds) ═══
            delay(1500)

        } catch (e: Exception) {
            Log.e(TAG, "fetchFcmToken failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Start Python Server
    // ═══════════════════════════════════════════════════════════════

    private suspend fun startPythonServer() = withContext(Dispatchers.IO) {
        try {
            val app = FamilyAdminApp.getInstance()
            if (app?.isPythonReady() != true) {
                Timber.w("Python not ready, skipping server start")
                return@withContext
            }

            // ═══ Import Python module ═══
            val py = com.chaquo.python.Python.getInstance()
            val serverModule = py.getModule("server")

            // ═══ Call start_server() in background thread ═══
            val result = serverModule.callAttr("start_server")
            Timber.i("Server result: %s", result.toString())

        } catch (e: Exception) {
            Log.e(TAG, "startPythonServer failed", e)
            Timber.w("Server will start later")
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Update Status Text
    // ═══════════════════════════════════════════════════════════════

    private suspend fun updateStatus(text: String) = withContext(Dispatchers.Main) {
        try {
            tvStatus.text = text
            Timber.d("Status: %s", text)
        } catch (e: Exception) {
            Log.e(TAG, "updateStatus failed", e)
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Navigate to Main
    // ═══════════════════════════════════════════════════════════════

    private fun navigateToMain() {
        try {
            Log.i(TAG, "Navigating to MainActivity")

            val intent = Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            }

            startActivity(intent)
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
            finish()

        } catch (e: Exception) {
            Log.e(TAG, "navigateToMain failed", e)
            finish()
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Lifecycle Overrides
    // ═══════════════════════════════════════════════════════════════

    override fun onPause() {
        super.onPause()
        // ═══ Stop animations to save battery ═══
        try {
            ivLogo.clearAnimation()
        } catch (e: Exception) {
            // Ignore
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        try {
            scope.coroutineContext[Job]?.cancel()
        } catch (e: Exception) {
            // Ignore
        }
    }

    override fun onBackPressed() {
        // ═══ Disable back button on splash ═══
        // Do nothing
    }
}
