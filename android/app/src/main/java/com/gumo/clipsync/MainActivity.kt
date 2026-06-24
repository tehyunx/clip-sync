package com.gumo.clipsync

import android.Manifest
import android.content.*
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.floatingactionbutton.FloatingActionButton
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var repo: ClipRepository
    private lateinit var adapter: ClipAdapter

    private lateinit var tvStatus: TextView

    private val notifPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { startSyncService() }

    private val clipUpdateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                "com.gumo.clipsync.CLIP_UPDATED" -> loadClips()
                "com.gumo.clipsync.STATUS_CHANGED" -> {
                    val connected = intent.getBooleanExtra("connected", false)
                    tvStatus.text = if (connected) "● 동기화 중" else "● 연결 중..."
                    tvStatus.setTextColor(if (connected) 0xFF2E7D32.toInt() else 0xFF888888.toInt())
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        repo = ClipRepository(this)
        tvStatus = findViewById(R.id.tvStatus)

        val recycler = findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.recyclerView)
        adapter = ClipAdapter(
            items = emptyList(),
            onPin = { item -> lifecycleScope.launch { repo.togglePin(item); loadClips() } },
            onEdit = { item -> showEditDialog(item) },
            onDelete = { item ->
                AlertDialog.Builder(this)
                    .setMessage("삭제할까요?")
                    .setPositiveButton("삭제") { _, _ ->
                        lifecycleScope.launch { repo.delete(item); loadClips() }
                    }
                    .setNegativeButton("취소", null)
                    .show()
            },
            onCopy = { item ->
                val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("clipsync", item.text))
            }
        )
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        // Android 13+ 알림 권한
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            notifPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startSyncService()
        }

        // 메모 추가 FAB
        findViewById<FloatingActionButton>(R.id.fabAddMemo).setOnClickListener {
            showAddMemoDialog()
        }

        loadClips()

        val filter = IntentFilter().apply {
            addAction("com.gumo.clipsync.CLIP_UPDATED")
            addAction("com.gumo.clipsync.STATUS_CHANGED")
        }
        registerReceiver(clipUpdateReceiver, filter, RECEIVER_NOT_EXPORTED)
    }

    private fun showAddMemoDialog() {
        val input = EditText(this).apply {
            hint = "메모를 입력하세요"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
        }
        AlertDialog.Builder(this)
            .setTitle("메모 추가")
            .setView(input)
            .setPositiveButton("저장") { _, _ ->
                val text = input.text.toString().trim()
                if (text.isNotBlank()) {
                    lifecycleScope.launch {
                        repo.addClip(text)
                        SyncService.sendClip(text)
                        loadClips()
                    }
                }
            }
            .setNegativeButton("취소", null)
            .show()
        input.requestFocus()
    }

    private fun startSyncService() {
        startForegroundService(Intent(this, SyncService::class.java))
    }

    private fun showEditDialog(item: ClipItem) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(item.text)
            setSelection(item.text.length)
        }
        AlertDialog.Builder(this)
            .setTitle("편집")
            .setView(input)
            .setPositiveButton("저장") { _, _ ->
                val newText = input.text.toString()
                if (newText.isNotBlank()) {
                    lifecycleScope.launch { repo.updateText(item, newText); loadClips() }
                }
            }
            .setNegativeButton("취소", null)
            .show()
    }

    private fun loadClips() {
        lifecycleScope.launch {
            val clips = repo.getAll()
            runOnUiThread { adapter.update(clips) }
        }
    }

    override fun onResume() {
        super.onResume()
        loadClips()
    }

    override fun onDestroy() {
        unregisterReceiver(clipUpdateReceiver)
        super.onDestroy()
    }
}
