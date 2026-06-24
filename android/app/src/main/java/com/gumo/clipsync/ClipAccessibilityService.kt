package com.gumo.clipsync

import android.accessibilityservice.AccessibilityService
import android.content.ClipboardManager
import android.content.Intent
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.*

class ClipAccessibilityService : AccessibilityService() {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private lateinit var clipManager: ClipboardManager

    private val clipListener = ClipboardManager.OnPrimaryClipChangedListener {
        val text = clipManager.primaryClip?.getItemAt(0)?.text?.toString() ?: return@OnPrimaryClipChangedListener
        if (text.isBlank() || text == SyncService.lastText) return@OnPrimaryClipChangedListener

        // 릴레이로 전송
        SyncService.sendClip(text)

        // 로컬 DB 저장
        scope.launch {
            ClipRepository(applicationContext).addClip(text)
            sendBroadcast(Intent("com.gumo.clipsync.CLIP_UPDATED"))
        }
    }

    override fun onServiceConnected() {
        clipManager = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        clipManager.addPrimaryClipChangedListener(clipListener)

        // SyncService 시작
        startForegroundService(Intent(this, SyncService::class.java))
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    override fun onDestroy() {
        scope.cancel()
        clipManager.removePrimaryClipChangedListener(clipListener)
        super.onDestroy()
    }
}
