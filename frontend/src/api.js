// 前端 API 封装：看板走 REST，AI 问答走 SSE 流式。
// 生产由 FastAPI 托管（同源），开发由 vite 代理 /api。
// 公网保护（决策 D12）：接口 401 → 发出 auth:required 事件 → App 切到登录页。

import { emit } from './bus'

async function readError(resp, fallback) {
  let detail = fallback
  try { detail = (await resp.json()).detail || detail } catch { /* 保留默认 */ }
  return detail
}

export async function getJSON(path) {
  const resp = await fetch(path)
  if (!resp.ok) {
    if (resp.status === 401) emit('auth:required')
    throw new Error(await readError(resp, resp.statusText))
  }
  return resp.json()
}

export async function postJSON(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(await readError(resp, resp.statusText))
  return resp.json()
}

/**
 * SSE 流式问答。onEvent 收到 {type: 'thinking'|'tool'|'delta'|'done'|'error', data}。
 * 返回 done 事件携带的完整历史（含 reasoning_content / tool_calls / 工具结果），
 * 前端原样保存，下一次请求全量回传 —— 追问上下文由此成立。
 * 401（会话过期）→ 弹登录页；429（限流）→ 展示后端限流文案。
 */
export async function chatStream(messages, onEvent) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })
  if (resp.status === 401) {
    emit('auth:required')
    throw new Error(await readError(resp, '会话已过期，请重新输入口令'))
  }
  if (resp.status === 429) {
    throw new Error(await readError(resp, 'AI 对话太频繁，请稍后再试'))
  }
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
