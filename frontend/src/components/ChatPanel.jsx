import { useEffect, useRef, useState } from 'react'
import { chatStream } from '../api'
import { emit, on } from '../bus'

const SUGGESTS = [
  '哪个品类的门店营业额最高？',
  '牛肉poke 六月卖了多少钱？',
  '客单价最近是涨了还是跌了？',
  '最近有哪些天营业额异常？',
]

// 聊天面板：SSE 流式 + 工具执行状态 + 追问上下文（done 历史全量回传）+ 图表联动
export default function ChatPanel() {
  const [history, setHistory] = useState([])
  const [stream, setStream] = useState('')
  const [chips, setChips] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [thinking, setThinking] = useState('')
  const [input, setInput] = useState('')
  const boxRef = useRef(null)

  const send = async (question) => {
    const text = (question ?? input).trim()
    if (!text) return
    if (busy) {
      // 其他组件代发（异常卡按钮）时 AI 可能还在答上一题：明确提示，不静默忽略
      setNotice('AI 正在回答上一个问题，请稍候再试')
      return
    }
    setInput('')
    setBusy(true)
    setError('')
    setNotice('')
    setChips([])
    setStream('')
    setThinking('')
    const messages = [...history, { role: 'user', content: text }]
    setHistory(messages)
    try {
      await chatStream(messages, (ev) => {
        if (ev.type === 'tool') {
          setChips((c) => [...c, ev.data])
          // 图表联动：把 AI 查询的区间/门店同步给看板，数字可对照验证
          if (ev.data.meta && ev.data.meta.start && ev.data.meta.end) {
            emit('linkage', ev.data.meta)
          }
        } else if (ev.type === 'thinking') {
          setThinking((t) => t + ev.data)
        } else if (ev.type === 'delta') {
          setStream((s) => s + ev.data)
        } else if (ev.type === 'done') {
          setHistory(ev.data.messages)
          setStream('')
          setThinking('')
        } else if (ev.type === 'error') {
          setError(ev.data.message || 'AI 服务暂时不可用')
        }
      })
    } catch (e) {
      setError(e.message || '网络错误')
    } finally {
      setBusy(false)
    }
  }

  // 其他组件（异常卡）通过事件总线让聊天面板发问；依赖 history+busy 保证闭包不过期
  useEffect(() => on('chat:ask', (q) => send(q)), [history, busy])

  // 自动滚到底部
  useEffect(() => {
    const box = boxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [history, stream, chips, thinking, notice, busy, error])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const renderMsg = (m, i) => {
    if (m.role === 'user' && m.content) {
      return <div key={i} className="msg user">{m.content}</div>
    }
    if (m.role === 'assistant') {
      const toolChips = (m.tool_calls || []).map((tc, j) => {
        const toolMsg = history.find((x) => x.role === 'tool' && x.tool_call_id === tc.id)
        let label = tc.function?.name
        if (toolMsg) {
          try {
            const r = JSON.parse(toolMsg.content)
            if (r.ok && r.meta?.start) label = `📊 查询 ${r.meta.start} ~ ${r.meta.end}`
            else label = `⚠ ${r.error || '查询失败'}`
          } catch { /* 保持原名 */ }
        }
        return <div key={j} className="tool-chip">{label}</div>
      })
      return (
        <div key={i}>
          {toolChips}
          {m.content ? <div className="msg ai">{m.content}</div> : null}
        </div>
      )
    }
    return null
  }

  return (
    <div className="chat">
      <div className="suggests">
        {SUGGESTS.map((s) => (
          <button key={s} className="chip" disabled={busy} onClick={() => send(s)}>{s}</button>
        ))}
      </div>
      <div className="msgs" ref={boxRef}>
        {history.map(renderMsg)}
        {chips.map((c, i) => (
          <div key={i} className={`tool-chip${busy ? ' busy' : ''}`}>{c.summary}</div>
        ))}
        {busy && !stream && (
          <div className="thinking">
            <div className="thinking-title">🤔 正在思考…</div>
            {thinking && (
              <details className="thinking-detail" open>
                <summary>查看思考过程（流式）</summary>
                <div className="thinking-body">{thinking}</div>
              </details>
            )}
          </div>
        )}
        {stream && <div className="msg ai">{stream}<span className="cursor" /></div>}
        {notice && <div className="msg notice">{notice}</div>}
        {error && <div className="msg err">{error}</div>}
        {history.length === 0 && !busy && (
          <div className="msg ai">你好，我是经营数据助手。可以直接问我数据问题，例如「牛肉poke 六月卖了多少钱？」—— 回答的数字都来自真实数据库查询，并会同步联动左侧图表区间。</div>
        )}
      </div>
      <div className="chat-input">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="问点数据，例如：那五月呢？（支持追问上下文）"
          disabled={busy}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()}>
          {busy ? '思考中…' : '发送'}
        </button>
      </div>
    </div>
  )
}
