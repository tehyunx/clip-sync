package com.gumo.clipsync

import android.content.Context

class ClipRepository(context: Context) {
    private val dao = ClipDatabase.get(context).clipDao()
    private val maxUnpinned = 100

    suspend fun getAll(): List<ClipItem> = dao.getAll()

    suspend fun addClip(text: String) {
        if (text.isBlank()) return
        val existing = dao.findByText(text)
        if (existing != null) {
            // 이미 있으면 시간만 갱신
            dao.update(existing.copy(createdAt = System.currentTimeMillis()))
            return
        }
        // 한도 초과 시 가장 오래된 비고정 항목 삭제
        while (dao.countUnpinned() >= maxUnpinned) {
            dao.deleteOldestUnpinned()
        }
        dao.insert(ClipItem(text = text))
    }

    suspend fun togglePin(item: ClipItem) = dao.update(item.copy(isPinned = !item.isPinned))

    suspend fun updateText(item: ClipItem, newText: String) = dao.update(item.copy(text = newText))

    suspend fun delete(item: ClipItem) = dao.delete(item)
}
