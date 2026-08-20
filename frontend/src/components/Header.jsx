// 顶栏：品牌 + 日期筛选（快捷区间 / 自定义 / 门店下拉）
export default function Header({ meta, filter, setFilter }) {
  const bounds = meta || { date_min: '', date_max: '' }

  const quick = [
    { label: '全部', start: '', end: '' },
    { label: '5 月', start: '2026-05-01', end: '2026-05-31' },
    { label: '6 月', start: '2026-06-01', end: '2026-06-30' },
    { label: '7 月', start: '2026-07-01', end: '2026-07-31' },
    { label: '最近 7 天', start: '2026-07-25', end: '2026-07-31' },
  ]

  const activeKey = quick.findIndex(
    (r) => r.start === filter.start && r.end === filter.end && filter.storeId === ''
  )

  return (
    <div className="topbar">
      <div className="brand">
        <h1>POKE ONE</h1>
        <span className="sub">连锁餐饮 · 经营看板 {bounds.date_min && `（数据 ${bounds.date_min} ~ ${bounds.date_max}）`}</span>
      </div>
      <div className="filters">
        {quick.map((r, i) => (
          <button
            key={r.label}
            className={`chip${i === activeKey ? ' active' : ''}`}
            onClick={() => setFilter((f) => ({ ...f, start: r.start, end: r.end, storeId: '' }))}
          >
            {r.label}
          </button>
        ))}
        <input
          type="date"
          value={filter.start}
          min={bounds.date_min}
          max={bounds.date_max}
          onChange={(e) => setFilter((f) => ({ ...f, start: e.target.value }))}
        />
        <span className="sub">~</span>
        <input
          type="date"
          value={filter.end}
          min={bounds.date_min}
          max={bounds.date_max}
          onChange={(e) => setFilter((f) => ({ ...f, end: e.target.value }))}
        />
        <select
          value={filter.storeId}
          onChange={(e) => setFilter((f) => ({ ...f, storeId: e.target.value }))}
        >
          <option value="">全部门店</option>
          {(meta?.stores || []).map((s) => (
            <option key={s.store_id} value={s.store_id}>
              {s.store_name}（{s.category}）
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
