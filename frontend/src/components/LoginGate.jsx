import { useState } from 'react'
import { postJSON } from '../api'

// 登录门（公网访问保护，决策 D12）：口令登录 → 后端签发 HMAC 签名 cookie 会话（24h）
export default function LoginGate({ onSuccess }) {
  const [pwd, setPwd] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    const p = pwd.trim()
    if (!p || busy) return
    setBusy(true)
    setErr('')
    try {
      const r = await postJSON('/api/auth/login', { password: p })
      if (r.ok) onSuccess()
      else setErr(r.error || '口令不正确')
    } catch (e2) {
      setErr(e2.message || '网络错误')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-gate">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <h1>POKE ONE</h1>
          <p>连锁餐饮经营看板 · AI 数据问答</p>
        </div>
        <input
          type="password"
          placeholder="访问口令"
          value={pwd}
          onChange={(e) => setPwd(e.target.value)}
          autoFocus
        />
        <button type="submit" disabled={busy || !pwd.trim()}>
          {busy ? '验证中…' : '进入看板'}
        </button>
        {err && <p className="login-err">{err}</p>}
        <p className="login-tip">请向分享链接的人索取访问口令</p>
      </form>
    </div>
  )
}
