package com.family.admin

import android.Manifest
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
import com.family.admin.services.TunnelService
import com.google.android.material.progressindicator.CircularProgressIndicator
import timber.log.Timber

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
        private const val REQ_NOTIF = 1001
        private const val REFRESH_MS = 2_000L
    }

    private lateinit var toolbar: Toolbar

    // Server views
    private lateinit var tvServerStatus: TextView
    private lateinit var tvServerResult: TextView
    private lateinit var tvServerPort: TextView
    private lateinit var tvServerError: TextView
    private lateinit var btnServerToggle: Button
    private lateinit var progressServer: CircularProgressIndicator

    // Tunnel views
    private lateinit var tvTunnelStatus: TextView
    private lateinit var tvTunnelUrl: TextView
    private lateinit var tvTunnelError: TextView
    private lateinit var btnTunnelToggle: Button
    private lateinit var progressTunnel: CircularProgressIndicator

    private val handler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            renderServer()
            renderTunnel()
            handler.postDelayed(this, REFRESH_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        Log.i(TAG, "onCreate")

        bindViews()
        setupToolbar()
        requestNotificationPermissionIfNeeded()

        if (!ServerService.isRunning) {
            Log.i(TAG, "Auto-start ServerService")
            ServerService.start(this)
        }

        btnServerToggle.setOnClickListener { onServerToggle() }
        btnTunnelToggle.setOnClickListener { onTunnelToggle() }
    }

    override fun onResume() {
        super.onResume()
        handler.post(refreshRunnable)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshRunnable)
    }

    private fun bindViews() {
        toolbar = findViewById(R.id.toolbar)
        tvServerStatus = findViewById(R.id.tvServerStatus)
        tvServerResult = findViewById(R.id.tvServerResult)
        tvServerPort = findViewById(R.id.tvServerPort)
        tvServerError = findViewById(R.id.tvServerError)
        btnServerToggle = findViewById(R.id.btnServerToggle)
        progressServer = findViewById(R.id.progressServer)
        tvTunnelStatus = findViewById(R.id.tvTunnelStatus)
        tvTunnelUrl = findViewById(R.id.tvTunnelUrl)
        tvTunnelError = findViewById(R.id.tvTunnelError)
        btnTunnelToggle = findViewById(R.id.btnTunnelToggle)
        progressTunnel = findViewById(R.id.progressTunnel)
    }

    private fun setupToolbar() {
        setSupportActionBar(toolbar)
        supportActionBar?.title = getString(R.string.app_name)
        supportActionBar?.subtitle = "v5.0.0"
    }

    // ═══ SERVER ═══

    private fun onServerToggle() {
        if (ServerService.isRunning) {
            ServerService.stop(this)
            Toast.makeText(this, "Stopping server…", Toast.LENGTH_SHORT).show()
        } else {
            ServerService.start(this)
            Toast.makeText(this, "Starting server…", Toast.LENGTH_SHORT).show()
        }
        handler.postDelayed({ renderServer() }, 400)
    }

    private fun renderServer() {
        val running = ServerService.isRunning
        val result = ServerService.lastResult
        val error = ServerService.startupError

        tvServerStatus.text = if (running) "🟢 Server: RUNNING" else "🔴 Server: STOPPED"
        tvServerResult.text = "Last: $result"
        tvServerPort.text = "Port: 5000 • /api/v1"

        if (!error.isNullOrBlank()) {
            tvServerError.visibility = View.VISIBLE
            tvServerError.text = "⚠ $error"
        } else {
            tvServerError.visibility = View.GONE
        }
        progressServer.visibility = View.GONE
        btnServerToggle.text = if (running) "🛑 Stop Server" else "▶ Start Server"
    }

    // ═══ TUNNEL — direct call, no Python bridge ═══

    private fun onTunnelToggle() {
        if (TunnelService.isRunning) {
            Log.i(TAG, "Stopping TunnelService directly")
            TunnelService.stop(this)
            Toast.makeText(this, "Stopping tunnel…", Toast.LENGTH_SHORT).show()
        } else {
            Log.i(TAG, "Starting TunnelService directly")
            TunnelService.start(this)
            Toast.makeText(this, "Starting tunnel…", Toast.LENGTH_SHORT).show()
        }
        handler.postDelayed({ renderTunnel() }, 500)
    }

    private fun renderTunnel() {
        val running = TunnelService.isRunning
        val url = TunnelService.publicUrl
        val error = TunnelService.lastError

        tvTunnelStatus.text = when {
            running && !url.isNullOrBlank() -> "🟢 Tunnel: ACTIVE"
            running -> "🟡 Tunnel: STARTING…"
            else -> "🔴 Tunnel: STOPPED"
        }

        if (!url.isNullOrBlank()) {
            tvTunnelUrl.visibility = View.VISIBLE
            tvTunnelUrl.text = "🌐 $url"
        } else {
            tvTunnelUrl.visibility = View.GONE
        }

        if (!error.isNullOrBlank()) {
            tvTunnelError.visibility = View.VISIBLE
            tvTunnelError.text = "⚠ $error"
        } else {
            tvTunnelError.visibility = View.GONE
        }
        progressTunnel.visibility = if (running && url.isNullOrBlank()) View.VISIBLE else View.GONE
        btnTunnelToggle.text = if (running) "🛑 Stop Tunnel" else "🌐 Start Tunnel"
    }

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
}
