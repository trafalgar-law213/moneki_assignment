import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { getJSON, fmtMoney } from '../api'

// 门店对比：营业额柱 + 客单价线（双轴），按营业额降序
export default function StoreCompare({ q }) {
  const ref = useRef(null)
  const [rows, setRows] = useState([])

  useEffect(() => {
    getJSON(`/api/dashboard/stores${q}`).then(setRows).catch(() => setRows([]))
  }, [q])

  useEffect(() => {
    if (!ref.current || !rows.length) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1c2129',
        borderColor: '#30363d',
        textStyle: { color: '#e6edf3', fontSize: 12 },
      },
      legend: { textStyle: { color: '#8b949e' }, top: 0 },
      grid: { left: 52, right: 52, top: 36, bottom: 24 },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.store_name),
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { color: '#e6edf3', fontSize: 11, interval: 0 },
      },
      yAxis: [
        {
          type: 'value',
          name: '营业额',
          nameTextStyle: { color: '#8b949e' },
          splitLine: { lineStyle: { color: '#21262d' } },
          axisLabel: { color: '#8b949e', formatter: (v) => (v >= 10000 ? `${v / 10000}万` : v) },
        },
        {
          type: 'value',
          name: '客单价',
          nameTextStyle: { color: '#8b949e' },
          splitLine: { show: false },
          axisLabel: { color: '#8b949e' },
        },
      ],
      series: [
        {
          name: '营业额',
          type: 'bar',
          data: rows.map((r) => r.revenue),
          itemStyle: { borderRadius: [4, 4, 0, 0], color: '#58a6ff' },
        },
        {
          name: '客单价',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          data: rows.map((r) => r.aov),
          lineStyle: { color: '#f0883e', width: 2 },
          itemStyle: { color: '#f0883e' },
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
