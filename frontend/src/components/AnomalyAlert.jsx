import { useEffect, useState } from 'react'
import { getJSON, fmtMoney } from '../api'
import { emit } from '../bus'

// 异常预警（z-score>2.5）+ 一键让 AI 出经营建议（与聊天面板事件总线解耦）
export default function AnomalyAlert({ q }) {
  const [anom, setAnom] = useState(null)
  const [sent, setSent] = useState(false)

  const askAI = () => {
    emit('chat:ask', '请基于当前筛选区间的数据，给出三条经营建议（数字必须来自工具查询）')
    setSent(true)
    setTimeout(() => setSent(false), 2500)
  }

  useEffect(() => {
    getJSON(`/api/dashboard/anomalies${q}`).then(setAnom).catch(() => setAnom(null))
  }, [q])

  const days = anom?.days || []

  return (
    <div>
      <ul className="anomaly-list">
        {days.length === 0 && <li className="empty">✓ 当前区间无异常销售日</li>}
        {days.slice(0, 8).map((d) => (
          <li key={`${d.date}-${d.store_id}`}>
            <span>{d.date}</span>
            <span>{d.store_name}</span>
            <span className={d.direction === '偏高' ? 'dir-up' : 'dir-down'}>
              {d.direction === '偏高' ? '▲ 偏高' : '▼ 偏低'}
            </span>
            <span>¥{fmtMoney(d.revenue)}（z={d.zscore}）</span>
          </li>
        ))}
        {days.length > 8 && <li>…另有 {days.length - 8} 天</li>}
      </ul>
      <div style={{ marginTop: 10 }}>
        <button className="btn-link" onClick={askAI}>
          {sent ? '✓ 已提问，请看右侧 AI 面板' : '✨ 让 AI 基于当前数据给经营建议 →'}
        </button>
      </div>
    </div>
  )
}
