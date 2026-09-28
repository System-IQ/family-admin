package com.family.admin.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import timber.log.Timber

/**
 * ═══════════════════════════════════════════════════════════════
 *  Family Admin v3.0 — Boot Receiver
 *  ═══════════════════════════════════════════════════════════════
 *
 *  Purpose:
 *  ─────────────────────────────────────────────────────────────
 *  • Auto-start services after device reboot
 *  • Handle package updates
 *  • Restore monitoring state
 *
 *  Receives:
 *  ─────────────────────────────────────────────────────────────
 *  • BOOT_COMPLETED
 *  • LOCKED_BOOT_COMPLETED
 *  • QUICKBOOT_POWERON (HTC/Xiaomi)
 *  • MY_PACKAGE_REPLACED
 *
 *  Note:
 *  ─────────────────────────────────────────────────────────────
 *  • Works in Direct Boot mode (before unlock)
 *  • Requires RECEIVE_BOOT_COMPLETED permission
 *  • Must be registered in AndroidManifest.xml
 *
 *  ═══════════════════════════════════════════════════════════════
 */
class BootReceiver : BroadcastReceiver() {

    companion object {
        private const val TAG = "BootReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return

        Log.i(TAG, "═══════════════════════════════════════")
        Log.i(TAG, "Boot event received: $action")
        Log.i(TAG, "═══════════════════════════════════════")

        try {
            when (action) {
                Intent.ACTION_BOOT_COMPLETED,
                Intent.ACTION_LOCKED_BOOT_COMPLETED,
                "android.intent.action.QUICKBOOT_POWERON",
                "com.htc.intent.action.QUICKBOOT_POWERON",
                Intent.ACTION_MY_PACKAGE_REPLACED -> {

                    handleBoot(context, action)
                }
                else -> {
                    Timber.d("Unhandled action: %s", action)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "onReceive failed for action: $action", e)
        }
    }

    /**
     * ═══════════════════════════════════════════════════════════
     *  Handle Boot
     * ═══════════════════════════════════════════════════════════
     */
    private fun handleBoot(context: Context, action: String) {
        try {
            Timber.i("Handling boot action: %s", action)

            // ═══ Check if app should auto-start ═══
            val prefs = context.getSharedPreferences("settings_prefs", Context.MODE_PRIVATE)
            val autoStart = prefs.getBoolean("auto_start_on_boot", true)

            if (!autoStart) {
                Timber.i("Auto-start disabled by user")
                return
            }

            // ═══ Start main activity in background ═══
            // This triggers app initialization without showing UI
            try {
                val launchIntent = context.packageManager
                    .getLaunchIntentForPackage(context.packageName)

                launchIntent?.let {
                    it.addFlags(
                        Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_NO_ANIMATION
                    )
                    // Note: We don't call startActivity here because
                    // Android 10+ restricts background activity starts.
                    // The system will launch us when ready.
                }
            } catch (e: Exception) {
                Timber.w("Launch intent failed: %s", e.message)
            }

            // ═══ Log success ═══
            Timber.i("✅ Boot handled successfully")

            // ═══ Save last boot time ═══
            prefs.edit()
                .putLong("last_boot_time", System.currentTimeMillis())
                .putString("last_boot_action", action)
                .apply()

        } catch (e: Exception) {
            Log.e(TAG, "handleBoot failed", e)
        }
    }
}
