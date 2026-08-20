import { useEffect, useState } from 'react'
import { getJSON } from './api'
import { on } from './bus'
import Header from './components/Header'
import KpiCards from './components/KpiCards'
import TrendChart from './components/TrendChart'
import TopProducts from './components/TopProducts'
import StoreCompare from './components/StoreCompare'
import AnomalyAlert from './components/AnomalyAlert'
import ChatPanel from './components/ChatPanel'
import LoginGate from './components/LoginGate'

export default function App() {
  const [meta, setMeta] = useState(null)
  const [filter, setFilter] = useState({ start: '', end: '', storeId: '' })
  const [authed, setAuthed] = useState(null) // null=检测中 / true=已登录 / false=需口令

  useEffect(() => {
    getJSON('/api/meta').then(setMeta).catch((e) => console.error(e))
  }, [])

  // 公网保护（决策 D12）：启动时检查会话；任何接口 401 → 切换到登录页
  useEffect(() => {
    getJSON('/api/auth/check')
      .then((r) => setAuthed(!!r.ok))
      .catch(() => setAuthed(false))
    return on('auth:required', () => setAuthed(false))
  }, [])

  // 图表联动：AI 工具事件携带查询区间 → 看板跳转到同一区间，方便对照验证
  useEffect(() => on('linkage', (m) => {
    if (!m || !m.start || !m.end) return
    setFilter((f) => ({
      ...f,
      start: m.start,
      end: m.end,
      storeId: m.store_ids && m.store_ids.length === 1 ? m.store_ids[0] : '',
    }))
  }), [])

  const qs = [
    filter.start && `start=${filter.start}`,
    filter.end && `end=${filter.end}`,
    filter.storeId && `store_id=${filter.storeId}`,
  ].filter(Boolean).join('&')
  const q = qs ? `?${qs}` : ''

  if (authed === null) return <div className="boot-screen">加载中…</div>
  if (!authed) return <LoginGate onSuccess={() => setAuthed(true)} />

  return (
    <>
      <Header meta={meta} filter={filter} setFilter={setFilter} />
      <KpiCards q={q} />
      <div className="grid">
        <div className="left-col">
          <div className="panel">
            <h3>营业额 & 客单价趋势</h3>
            <TrendChart q={q} />
          </div>
          <div className="panel">
            <h3>Top 10 商品（营业额）</h3>
            <TopProducts q={q} />
          </div>
          <div className="panel">
            <h3>门店对比</h3>
            <StoreCompare q={q} />
          </div>
        </div>
        <div className="right-col">
          <div className="panel">
            <h3>AI 数据问答</h3>
            <ChatPanel />
          </div>
          <div className="panel">
            <h3>异常预警 · 经营建议</h3>
            <AnomalyAlert q={q} />
          </div>
        </div>
      </div>
      <div className="footer-note">
        数据来源：5 家门店 POS 真实导出（脱敏），经清洗入库 {meta ? `${meta.sales_rows.toLocaleString('zh-CN')} 行` : '…'}；
        AI 回答的数字全部来自与看板同一套数据库查询（唯一取数层），可在左侧图表对照验证。
      </div>
    </>
  )
}
