package com.gumo.clipsync

import android.app.Activity
import android.content.ClipboardManager
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import kotlinx.coroutines.*

// 투명 Activity — 공유 수신 + 클립보드 읽기
class ClipReaderActivity : Activity() {

    private var handled = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setBackgroundDrawableResource(android.R.color.transparent)
        window.addFlags(WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL)
        val lp = window.attributes
        lp.width = 1
        lp.height = 1
        lp.alpha = 0f
        window.attributes = lp

        // 공유 인텐트는 포커스 없이도 EXTRA_TEXT를 바로 읽을 수 있음
        val sharedText = intent?.getStringExtra(Intent.EXTRA_TEXT)
        if (!sharedText.isNullOrBlank()) {
            handled = true
            saveAndSync(sharedText)
            // 서비스도 같이 시작 (연결 유지용)
            startForegroundService(Intent(this, SyncService::class.java))
            finish()
        }
    }

    // 공유가 아닌 경우 (SyncService 리스너 → 클립보드 읽기) — 포커스 필요
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && !handled) {
            handled = true
            val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            val text = try {
                cm.primaryClip?.getItemAt(0)?.text?.toString()
            } catch (_: Exception) { null }
            if (!text.isNullOrBlank()) saveAndSync(text)
            finish()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    private fun saveAndSync(text: String) {
        if (text == SyncService.lastText) return
        // 서비스 시작 보장 (이미 실행 중이면 no-op)
        startForegroundService(Intent(this, SyncService::class.java))
        CoroutineScope(Dispatchers.IO).launch {
            ClipRepository(applicationContext).addClip(text)
            sendBroadcast(Intent("com.gumo.clipsync.CLIP_UPDATED"))
            // WebSocket 연결될 때까지 최대 3초 대기
            var waited = 0
            while (SyncService.webSocket == null && waited < 3000) {
                delay(100); waited += 100
            }
            // sendClip이 lastText 설정 + 전송 담당 (여기서 lastText 미리 건드리면 안 됨)
            SyncService.sendClip(text)
        }
    }

    override fun onStop() {
        super.onStop()
        if (!handled) finish()
    }
}
