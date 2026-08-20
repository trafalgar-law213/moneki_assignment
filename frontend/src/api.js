// 前端 API 封装：看板走 REST，AI 问答走 SSE 流式。
// 生产由 FastAPI 托管（同源），开发由 vite 代理 /api。

export async function getJSON(path) {
  const resp = await fetch(path)
  if (!resp.ok) {
    let detail = resp.statusText
    try { detail = (await resp.json()).detail || detail } catch { /* 保留默认 */ }
    throw new Error(detail)
  }
  return resp.json()
}

/**
 * SSE 流式问答。onEvent 收到 {type: 'tool'|'delta'|'done'|'error', data}。
 * 返回 done 事件携带的完整历史（含 reasoning_content / tool_calls / 工具结果），
 * 前端原样保存，下一次请求全量回传 —— 追问上下文由此成立。
 */
export async function chatStream(messages, onEvent) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败（HTTP ${resp.status}）`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop()
    for (const part of parts) {
      const line = part.trim()
      if (line.startsWith('data: ')) {
        onEvent(JSON.parse(line.slice(6)))
      }
    }
  }
}

export function fmtMoney(n) {
  return Number(n ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
