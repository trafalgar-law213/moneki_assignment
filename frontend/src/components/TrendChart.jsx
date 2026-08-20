import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { getJSON, fmtMoney } from '../api'

// 营业额趋势（柱）+ 客单价（线，双轴）+ 异常日标记点
export default function TrendChart({ q }) {
  const ref = useRef(null)
  const [data, setData] = useState(null)
  const [anom, setAnom] = useState(null)

  useEffect(() => {
    getJSON(`/api/dashboard/summary${q}`).then(setData).catch(() => setData(null))
    getJSON(`/api/dashboard/anomalies${q}`).then(setAnom).catch(() => setAnom(null))
  }, [q])

  useEffect(() => {
    if (!ref.current || !data) return
    const chart = echarts.init(ref.current)
    const dates = data.by_day.map((d) => d.date)

    const markPoints = (anom?.days || []).map((a) => ({
      coord: [a.date, a.revenue],
      value: a.direction,
      symbol: 'pin',
      symbolSize: 30,
      itemStyle: { color: a.direction === '偏高' ? '#f85149' : '#f0883e' },
      label: { formatter: `${a.store_name}`, fontSize: 10, color: '#e6edf3' },
    }))

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1c2129',
        borderColor: '#30363d',
        textStyle: { color: '#e6edf3', fontSize: 12 },
        valueFormatter: (v) => (typeof v === 'number' ? fmtMoney(v) : v),
      },
      legend: { textStyle: { color: '#8b949e' }, top: 0 },
      grid: { left: 60, right: 56, top: 36, bottom: 28 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { color: '#8b949e', fontSize: 11 },
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
          data: data.by_day.map((d) => d.revenue),
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#58a6ff' },
              { offset: 1, color: 'rgba(88,166,255,.15)' },
            ]),
          },
          markPoint: { data: markPoints, animation: false },
        },
        {
          name: '客单价',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          data: data.by_day.map((d) => d.aov),
          lineStyle: { color: '#bc8cff', width: 2 },
          itemStyle: { color: '#bc8cff' },
        },
      ],
    })

    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [data, anom])

  return <div ref={ref} className="chart" />
}
