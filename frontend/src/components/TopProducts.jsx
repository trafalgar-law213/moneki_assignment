import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { getJSON, fmtMoney } from '../api'

// Top 10 商品横向条形图
export default function TopProducts({ q }) {
  const ref = useRef(null)
  const [rows, setRows] = useState([])

  useEffect(() => {
    getJSON(`/api/dashboard/top-products${q ? `${q}&` : '?'}limit=10`).then(setRows).catch(() => setRows([]))
  }, [q])

  useEffect(() => {
    if (!ref.current || !rows.length) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        backgroundColor: '#1c2129',
        borderColor: '#30363d',
        textStyle: { color: '#e6edf3', fontSize: 12 },
        formatter: (p) => {
          const r = rows[p.dataIndex]
          return `${r.name}（${r.category}）<br/>营业额 ¥${fmtMoney(r.revenue)}<br/>订单 ${r.orders} · 数量 ${r.qty} · 单价 ¥${fmtMoney(r.unit_price)}`
        },
      },
      grid: { left: 110, right: 60, top: 10, bottom: 24 },
      xAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#21262d' } },
        axisLabel: { color: '#8b949e', formatter: (v) => (v >= 10000 ? `${v / 10000}万` : v) },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: rows.map((r) => r.name),
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { color: '#e6edf3', fontSize: 11 },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.revenue),
          itemStyle: {
            borderRadius: [0, 4, 4, 0],
            color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
              { offset: 0, color: '#3fb950' },
              { offset: 1, color: 'rgba(63,185,80,.12)' },
            ]),
          },
          label: {
            show: true,
            position: 'right',
            color: '#8b949e',
            fontSize: 10,
            formatter: (p) => fmtMoney(p.value),
          },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [rows])

  return <div ref={ref} className="chart" />
}
