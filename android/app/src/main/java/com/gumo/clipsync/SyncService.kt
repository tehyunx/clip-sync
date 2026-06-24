package com.gumo.clipsync

import android.app.*
import android.content.*
import android.os.IBinder
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONObject

class SyncService : Service() {

    companion object {
        const val RELAY_URL = "wss://clipboard.35.209.129.27.sslip.io/ws"
        const val TOKEN = "clipsync-secret-2026"
        const val DEVICE_NAME = "android"
        const val CHANNEL_ID = "clipsync_channel"
        const val NOTIF_ID = 1
        const val ACTION_PAUSE = "com.gumo.clipsync.PAUSE"
        const val ACTION_RESUME = "com.gumo.clipsync.RESUME"
        const val ACTION_CLEAR = "com.gumo.clipsync.CLEAR"

        var lastText = ""
        var webSocket: WebSocket? = null
        var isPaused = false
        var suppressNextChange = false

        fun sendClip(text: String, context: android.content.Context? = null) {
            if (isPaused) return
            if (text == lastText) return
            lastText = text
            val ws = webSocket
            if (ws == null) {
                android.util.Log.w("ClipSync", "sendClip: webSocket null")
                return
            }
            val msg = JSONObject().apply {
                put("type", "clip")
                put("text", text)
            }.toString()
            val sent = ws.send(msg)
            android.util.Log.d("ClipSync", "sendClip: ${if (sent) "OK" else "FAIL"} | ${text.take(30)}")
        }
    }

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var client: OkHttpClient? = null
    private val actionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                ACTION_PAUSE -> { isPaused = true; updateNotification() }
                ACTION_RESUME -> { isPaused = false; updateNotification() }
                ACTION_CLEAR -> scope.launch {
                    val db = ClipDatabase.get(applicationContext)
                    db.clipDao().getAll().forEach { db.clipDao().delete(it) }
                    sendBroadcast(Intent("com.gumo.clipsync.CLIP_UPDATED"))
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification())

        val filter = IntentFilter().apply {
            addAction(ACTION_PAUSE)
            addAction(ACTION_RESUME)
            addAction(ACTION_CLEAR)
        }
        registerReceiver(actionReceiver, filter, RECEIVER_NOT_EXPORTED)

        connect()
    }

    private fun connect() {
        client = OkHttpClient()
        val request = Request.Builder().url(RELAY_URL).build()

        client!!.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                webSocket = ws
                ws.send(JSONObject().apply {
                    put("token", TOKEN)
                    put("name", DEVICE_NAME)
                }.toString())
                updateNotification()
                sendBroadcast(Intent("com.gumo.clipsync.STATUS_CHANGED").putExtra("connected", true))
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val msg = JSONObject(text)
                    if (msg.optString("type") == "clip") {
                        val received = msg.optString("text")
                        if (received.isNotBlank() && received != lastText) {
                            lastText = received
                            setClipboard(received)
                            scope.launch {
                                ClipRepository(applicationContext).addClip(received)
                                sendBroadcast(Intent("com.gumo.clipsync.CLIP_UPDATED"))
                            }
                        }
                    }
                } catch (_: Exception) {}
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                webSocket = null
                updateNotification()
                sendBroadcast(Intent("com.gumo.clipsync.STATUS_CHANGED").putExtra("connected", false))
                scope.launch { delay(5000); connect() }
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                webSocket = null
                updateNotification()
                sendBroadcast(Intent("com.gumo.clipsync.STATUS_CHANGED").putExtra("connected", false))
                scope.launch { delay(3000); connect() }
            }
        })
    }

    private fun setClipboard(text: String) {
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("clipsync", text))
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, buildNotification())
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel),
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply { setSound(null, null) }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )

        val connected = webSocket != null
        val statusText = when {
            isPaused -> "일시정지됨"
            connected -> "동기화 중 ●"
            else -> "연결 중..."
        }

        // Pause/Resume 액션
        val pauseResumeAction = if (isPaused) {
            val intent = PendingIntent.getBroadcast(this, 1,
                Intent(ACTION_RESUME), PendingIntent.FLAG_IMMUTABLE)
            Notification.Action.Builder(null, "재개", intent).build()
        } else {
            val intent = PendingIntent.getBroadcast(this, 2,
                Intent(ACTION_PAUSE), PendingIntent.FLAG_IMMUTABLE)
            Notification.Action.Builder(null, "일시정지", intent).build()
        }

        // Clear 액션
        val clearIntent = PendingIntent.getBroadcast(this, 3,
            Intent(ACTION_CLEAR), PendingIntent.FLAG_IMMUTABLE)
        val clearAction = Notification.Action.Builder(null, "기록 지우기", clearIntent).build()

        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("ClipSync — $statusText")
            .setContentText("탭하면 클립보드 기록을 볼 수 있습니다")
            .setSmallIcon(android.R.drawable.ic_menu_share)
            .setContentIntent(openIntent)
            .setOngoing(true)
            .addAction(pauseResumeAction)
            .addAction(clearAction)
            .build()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_STICKY

    override fun onDestroy() {
        scope.cancel()
        unregisterReceiver(actionReceiver)
        webSocket?.close(1000, "stopped")
        client?.dispatcher?.executorService?.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
