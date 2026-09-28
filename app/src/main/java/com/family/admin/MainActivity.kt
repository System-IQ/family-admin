package com.family.admin

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.family.admin.services.ServerService
import com.google.android.material.progressindicator.CircularProgressIndicator
import timber.log.Timber

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v5.0 — MainActivity
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Responsibilities (Phase 4.5):
 *    • Start/stop ServerService on user action
 *    • Reflect real server status from ServerService
 *    • Request runtime permissions (notifications)
 *
 *  Not yet implemented (Phase 5+):
 *    • Dashboard UI
 *    • Device list
 *    • Maps
 * ═══════════════════════════════════════════════════════════════
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
        private const val REQ_NOTIF = 1001
        private const val STATUS_REFRESH_MS = 2_000L
    }

    // Views
    private lateinit var toolbar: Toolbar
    private lateinit var tvStatus: TextView
    private lateinit var tvResult: TextView
    private lateinit var tvPort: TextView
    private lateinit var tvError: TextView
    private lateinit var btnToggle: Button
    private lateinit var progressBar: CircularProgressIndicator

    // Refresh loop
    private val handler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            renderStatus()
            handler.postDelayed(this, STATUS_REFRESH_MS)
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  Lifecycle
    // ═══════════════════════════════════════════════════════════

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        Log.i(TAG, "MainActivity created")

        bindViews()
        setupToolbar()
        requestNotificationPermissionIfNeeded()

        // Auto-start the server on first launch
        if (!ServerService.isRunning) {
            Log.i(TAG, "Auto-starting ServerService")
            ServerService.start(this)
        }

        btnToggle.setOnClickListener { onToggleClicked() }
    }

    override fun onResume() {
        super.onResume()
        handler.post(refreshRunnable)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshRunnable)
    }

    // ═══════════════════════════════════════════════════════════
    //  Views
    // ═══════════════════════════════════════════════════════════

    private fun bindViews() {
        toolbar = findViewById(R.id.toolbar)
        tvStatus = findViewById(R.id.tvStatus)
        tvResult = findViewById(R.id.tvResult)
        tvPort = findViewById(R.id.tvPort)
        tvError = findViewById(R.id.tvError)
        btnToggle = findViewById(R.id.btnToggle)
        progressBar = findViewById(R.id.progressBar)
    }

    private fun setupToolbar() {
        setSupportActionBar(toolbar)
        supportActionBar?.title = getString(R.string.app_name)
        supportActionBar?.subtitle = getString(R.string.app_version)
    }

    // ═══════════════════════════════════════════════════════════
    //  Button handler
    // ═══════════════════════════════════════════════════════════

    private fun onToggleClicked() {
        if (ServerService.isRunning) {
            Log.i(TAG, "User requested server stop")
            ServerService.stop(this)
            Toast.makeText(this, "Stopping server…", Toast.LENGTH_SHORT).show()
        } else {
            Log.i(TAG, "User requested server start")
            ServerService.start(this)
            Toast.makeText(this, "Starting server…", Toast.LENGTH_SHORT).show()
        }
        // status will refresh automatically
        handler.postDelayed({ renderStatus() }, 500)
    }

    // ═══════════════════════════════════════════════════════════
    //  Status rendering
    // ═══════════════════════════════════════════════════════════

    private fun renderStatus() {
        val running = ServerService.isRunning
        val result = ServerService.lastResult
        val error = ServerService.startupError

        // Status line
        tvStatus.text = if (running) "🟢 Server: RUNNING" else "🔴 Server: STOPPED"

        // Result line
        tvResult.text = "Last result: $result"

        // Port line
        tvPort.text = "Port: 5000  •  API: /api/v1"

        // Error line
        if (!error.isNullOrBlank()) {
            tvError.visibility = View.VISIBLE
            tvError.text = "⚠ $error"
        } else {
            tvError.visibility = View.GONE
        }

        // Progress bar — only if starting but not yet running
        if (!running && result != "stopped" && result != "not_started") {
            progressBar.visibility = View.VISIBLE
        } else {
            progressBar.visibility = View.GONE
        }

        // Button label
        btnToggle.text = if (running) "🛑 Stop Server" else "▶ Start Server"

        Timber.d("Status: running=$running, result=$result, error=$error")
    }

    // ═══════════════════════════════════════════════════════════
    //  Permissions
    // ═══════════════════════════════════════════════════════════

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED

            if (!granted) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    REQ_NOTIF
                )
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_NOTIF) {
            val granted = grantResults.isNotEmpty() &&
                    grantResults[0] == PackageManager.PERMISSION_GRANTED
            Timber.i("Notification permission granted=$granted")
        }
    }
}
