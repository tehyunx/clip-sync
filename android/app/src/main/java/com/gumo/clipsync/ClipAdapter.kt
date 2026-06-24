package com.gumo.clipsync

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.cardview.widget.CardView
import androidx.recyclerview.widget.RecyclerView

class ClipAdapter(
    private var items: List<ClipItem>,
    private val onPin: (ClipItem) -> Unit,
    private val onEdit: (ClipItem) -> Unit,
    private val onDelete: (ClipItem) -> Unit,
    private val onCopy: (ClipItem) -> Unit,
) : RecyclerView.Adapter<ClipAdapter.VH>() {

    inner class VH(val card: CardView) : RecyclerView.ViewHolder(card) {
        val tvText: TextView = card.findViewById(R.id.tvText)
        val btnPin: Button = card.findViewById(R.id.btnPin)
        val btnEdit: Button = card.findViewById(R.id.btnEdit)
        val btnDelete: Button = card.findViewById(R.id.btnDelete)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_clip, parent, false) as CardView
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.tvText.text = item.text

        // 고정 항목은 배경 강조
        holder.card.setCardBackgroundColor(
            if (item.isPinned) 0xFFFFF8E1.toInt() else 0xFFFFFFFF.toInt()
        )
        holder.btnPin.text = if (item.isPinned) "📌 고정됨" else "📌 고정"

        holder.card.setOnClickListener { onCopy(item) }
        holder.btnPin.setOnClickListener { onPin(item) }
        holder.btnEdit.setOnClickListener { onEdit(item) }
        holder.btnDelete.setOnClickListener { onDelete(item) }
    }

    override fun getItemCount() = items.size

    fun update(newItems: List<ClipItem>) {
        items = newItems
        notifyDataSetChanged()
    }
}
