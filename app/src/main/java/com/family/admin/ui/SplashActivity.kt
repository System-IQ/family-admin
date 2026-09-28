package com.family.admin.ui

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.animation.AnimationUtils
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.family.admin.FamilyAdminApp
import com.family.admin.MainActivity
import com.family.admin.R
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import timber.log.Timber

class SplashActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "SplashActivity"
        private const val MIN_SPLASH_DURATION = 1500L
        private const val MAX_WAIT_TIME = 5000L
        private const val PREFS_FCM = "fcm_token"
        private const val KEY_FCM_TOKEN = "current_token"
    }

    private lateinit var ivLogo: ImageView
    private lateinit var tvAppName: TextView
    private lateinit var tvVersion: TextView
    private lateinit var tvStatus: TextView

    private val scope = CoroutineScope(Dispatchers.Main + Job())
    private var isReady = false
    private var startTime = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        val splashScreen = installSplashScreen()
        splashScreen.setKeepOnScreenCondition { !isReady }
        super.onCreate(savedInstanceState)

        startTime = System.currentTimeMillis()
        Log.i(TAG, "Splash Screen — Family Admin v3.0")

        setContentView(R.layout.activity_splash)

        bindViews()
        applyEntranceAnimations()
        initializeApp()
    }

    private fun bindViews() {
        try {
            ivLogo = findViewById(R.id.ivLogo)
            tvAppName = findViewById(R.id.tvAppName)
            tvVersion = findViewById(R.id.tvVersion)
            tvStatus = findViewById(R.id.tvStatus)

            tvVersion.text = getString(R.string.app_version)
            tvStatus.text = getString(R.string.server_starting)
        } catch (e: Exception) {
            Log.e(TAG, "bindViews failed", e)
        }
    }

    private fun applyEntranceAnimations() {
        try {
            listOf(ivLogo, tvAppName, tvVersion, tvStatus).forEachIndexed { index, view ->
                val anim = AnimationUtils.loadAnimation(this, android.R.anim.fade_in)
                anim.duration = 800L
                anim.startOffset = index * 200L
                view.startAnimation(anim)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Animations failed", e)
        }
    }

    private fun initializeApp() {
        scope.launch {
            try {
                updateStatus("Loading...")
                delay(500)

                updateStatus("Checking components...")
                checkAppComponents()

                updateStatus("Connecting to server...")
                fetchFcmToken()

                updateStatus("Starting server...")
                startPythonServer()

                val elapsed = System.currentTimeMillis() - startTime
                val remaining = MIN_SPLASH_DURATION - elapsed
                if (remaining > 0) delay(remaining)

                updateStatus("Ready ✅")
                delay(300)

                isReady = true
                navigateToMain()
            } catch (e: Exception) {
                Log.e(TAG, "Init failed", e)
                updateStatus("Error: ${e.message}")
                delay(2000)
                isReady = true
                navigateToMain()
            }
        }

        Handler(Looper.getMainLooper()).postDelayed({
            if (!isReady) {
                Log.w(TAG, "Safety timeout")
                isReady = true
                navigateToMain()
            }
        }, MAX_WAIT_TIME)
    }

    private suspend fun checkAppComponents() = withContext(Dispatchers.IO) {
        try {
            val app = FamilyAdminApp.getInstance() ?: return@withContext
            Timber.d("Firebase: ${app.isFirebaseReady()}")
            Timber.d("Channels: ${app.isChannelsReady()}")
            Timber.d("Python: ${app.isPythonReady()}")
        } catch (e: Exception) {
            Log.e(TAG, "checkAppComponents failed", e)
        }
    }

    private suspend fun fetchFcmToken() = withContext(Dispatchers.IO) {
        try {
            val prefs = getSharedPreferences(PREFS_FCM, MODE_PRIVATE)
            val existing = prefs.getString(KEY_FCM_TOKEN, null)
            if (!existing.isNullOrEmpty()) return@withContext

            FirebaseMessaging.getInstance().token
                .addOnCompleteListener { task ->
                    if (task.isSuccessful) {
                        task.result?.let { token ->
                            prefs.edit().putString(KEY_FCM_TOKEN, token).apply()
                            Timber.i("FCM token cached")
                        }
                    }
                }
            delay(1500)
        } catch (e: Exception) {
            Log.e(TAG, "fetchFcmToken failed", e)
        }
    }

    private suspend fun startPythonServer() = withContext(Dispatchers.IO) {
        try {
            val app = FamilyAdminApp.getInstance()
            if (app?.isPythonReady() != true) return@withContext

            val py = com.chaquo.python.Python.getInstance()
            val serverModule = py.getModule("server")
            val result = serverModule.callAttr("start_server")
            Timber.i("Server: ${result}")
        } catch (e: Exception) {
            Log.e(TAG, "Python server failed", e)
        }
    }

    private suspend fun updateStatus(text: String) = withContext(Dispatchers.Main) {
        try {
            tvStatus.text = text
        } catch (e: Exception) {
            Log.e(TAG, "updateStatus failed", e)
        }
    }

    private fun navigateToMain() {
        try {
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

    override fun onDestroy() {
        super.onDestroy()
        try {
            scope.coroutineContext[Job]?.cancel()
        } catch (_: Exception) {}
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // Disabled
    }
}
