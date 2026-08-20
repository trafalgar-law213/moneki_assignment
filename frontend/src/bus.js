// 轻量事件总线：组件间解耦联动。
// - 'linkage'：AI 工具事件携带查询区间 → 看板跳转到对应区间（图表联动）
// - 'chat:ask'：其他组件让聊天面板发一个问题（如「让 AI 分析经营建议」）
// - 'auth:required'：接口 401（未登录/会话过期）→ App 切换到登录页（公网保护）

const listeners = new Map()

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set())
  listeners.get(event).add(fn)
  return () => listeners.get(event)?.delete(fn)
}

export function emit(event, payload) {
  listeners.get(event)?.forEach((fn) => fn(payload))
}
