import { useEffect, useState } from 'react'
import { getJSON, fmtMoney } from '../api'

// KPI 卡：营业额 / 订单数 / 客单价 + 环比徽标 + 区间异常徽标
export default function KpiCards({ q }) {
  const [data, setData] = useState(null)
  const [anom, setAnom] = useState(null)

  useEffect(() => {
    getJSON(`/api/dashboard/summary${q}`).then(setData).catch(() => setData(null))
    getJSON(`/api/dashboard/anomalies${q}`).then(setAnom).catch(() => setAnom(null))
  }, [q])

  const kpis = [
    { label: '营业额', value: data?.revenue, unit: '元', prev: data?.prev_period?.revenue, money: true },
    { label: '订单数', value: data?.orders, unit: '单', prev: data?.prev_period?.orders },
    { label: '客单价', value: data?.aov, unit: '元', prev: data?.prev_period?.aov, money: true },
  ]

  const anomDays = anom?.days?.length || 0

  return (
    <div className="kpi-row">
      {kpis.map((k) => {
        let badge = null
        if (k.prev && k.value != null) {
          const pct = ((k.value - k.prev) / k.prev) * 100
          badge =
            Math.abs(pct) < 0.05 ? (
              <span className="badge flat">环比持平</span>
            ) : pct > 0 ? (
              <span className="badge up">环比 +{pct.toFixed(1)}%</span>
            ) : (
              <span className="badge down">环比 {pct.toFixed(1)}%</span>
            )
        }
        return (
          <div className="kpi" key={k.label}>
            <div className="label">{k.label}{data ? `（${data.start} ~ ${data.end}）` : ''}</div>
            <div className="value">
              {data ? (k.money ? fmtMoney(k.value) : Number(k.value).toLocaleString('zh-CN')) : '—'}
              <span className="unit">{k.unit}</span>
            </div>
            <div className="meta">
              {badge}
              {k.label === '营业额' && anomDays > 0 && (
                <span className="badge warn">⚠ {anomDays} 天异常</span>
              )}
              {k.label === '营业额' && anomDays === 0 && data && (
                <span className="badge flat">区间内无异常</span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
