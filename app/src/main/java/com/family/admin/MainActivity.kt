package com.family.admin

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import com.google.android.material.progressindicator.CircularProgressIndicator
import timber.log.Timber

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v3.0 — MainActivity
 *  ═══════════════════════════════════════════════════════════════
 *  Placeholder — Full UI comes in Stage 4
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    private lateinit var toolbar: Toolbar
    private lateinit var tvStatus: TextView
    private lateinit var progressBar: CircularProgressIndicator

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        Log.i(TAG, "MainActivity started")
        Timber.d("Family Admin v3.0 — Main")

        bindViews()
        setupToolbar()
        initializeServer()

        // ═══ Handle intent from notification ═══
        handleIntent(intent)
    }

    private fun bindViews() {
        try {
            toolbar = findViewById(R.id.toolbar)
            tvStatus = findViewById(R.id.tvStatus)
            progressBar = findViewById(R.id.progressBar)
        } catch (e: Exception) {
            Log.e(TAG, "bindViews failed", e)
        }
    }

    private fun setupToolbar() {
        try {
            setSupportActionBar(toolbar)
            supportActionBar?.title = getString(R.string.app_name)
            supportActionBar?.subtitle = getString(R.string.app_version)
        } catch (e: Exception) {
            Log.e(TAG, "setupToolbar failed", e)
        }
    }

    private fun initializeServer() {
        try {
            tvStatus.text = "Server initializing..."

            val app = FamilyAdminApp.getInstance()
            val pyReady = app?.isPythonReady() ?: false

            if (pyReady) {
                tvStatus.text = "Server ready ✅"
                progressBar.visibility = View.GONE
                Timber.i("Python ready, server should be running")
            } else {
                tvStatus.text = "Python not ready"
                progressBar.visibility = View.GONE
                Timber.w("Python not ready")
            }
        } catch (e: Exception) {
            Log.e(TAG, "initializeServer failed", e)
            tvStatus.text = "Error: ${e.message}"
            progressBar.visibility = View.GONE
        }
    }

    private fun handleIntent(intent: Intent?) {
        try {
            val deviceId = intent?.getStringExtra("device_id")
            val fromNotif = intent?.getBooleanExtra("from_notification", false) ?: false

            if (fromNotif && !deviceId.isNullOrEmpty()) {
                Timber.i("Opened from notification: $deviceId")
                // TODO: Navigate to device detail (Stage 4)
            }
        } catch (e: Exception) {
            Log.e(TAG, "handleIntent failed", e)
        }
    }

    override fun onCreateOptionsMenu(menu: Menu?): Boolean {
        // TODO: Add menu in Stage 4
        return super.onCreateOptionsMenu(menu)
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return super.onOptionsItemSelected(item)
    }

    override fun onResume() {
        super.onResume()
        Timber.d("MainActivity resumed")
    }

    override fun onDestroy() {
        super.onDestroy()
        Timber.d("MainActivity destroyed")
    }
}
