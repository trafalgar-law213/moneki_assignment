import { useEffect, useState } from 'react'
import { getJSON } from '../api'
import { on } from '../bus'

// 历史问答面板：跨会话长期记忆（pgvector 语义检索）。
// 列表 = 最近问答（关掉浏览器再回来仍在）；搜索 = 语义相似检索（换种问法也能找到）。
export default function HistoryPanel() {
  const [items, setItems] = useState([])
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [err, setErr] = useState('')

  const loadRecent = () => {
    getJSON('/api/history/recent?limit=10')
      .then(setItems)
      .catch(() => setItems([]))
  }

  useEffect(loadRecent, [])
  // 每完成一轮问答，刷新列表（跨会话记忆即时体现）
  useEffect(() => on('chat:done', loadRecent), [])

  const search = async () => {
    const text = q.trim()
    if (!text) return loadRecent()
    setSearching(true)
    setErr('')
    try {
      setItems(await getJSON(`/api/history/search?q=${encodeURIComponent(text)}&limit=10`))
    } catch (e) {
      setErr(e.message || '检索失败')
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="history">
      <div className="chat-input">
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              search()
            }
          }}
          placeholder="语义搜索历史，例如：牛肉poke 卖了多少钱（换种问法也能找到）"
        />
        <button onClick={search} disabled={searching}>{searching ? '检索中…' : '搜索'}</button>
      </div>
      {err && <div className="msg err">{err}</div>}
      <div className="msgs history-list">
        {items.map((h) => (
          <div key={h.id} className="msg ai history-item">
            {h.similarity != null && (
              <span className="chip">相似度 {Math.round(h.similarity * 100)}%</span>
            )}
            <div className="history-q">❓ {h.question}</div>
            <div className="history-a">{h.answer}</div>
          </div>
        ))}
        {items.length === 0 && (
          <div className="msg ai">还没有历史问答。问几个数据问题，这里会跨会话记住它们。</div>
        )}
      </div>
    </div>
  )
}
