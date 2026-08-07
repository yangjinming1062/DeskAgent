import { useState } from 'react'
import { ApiClient, type TokenResponse } from '../api'
import { $baseUrl, setAuth } from '../state/auth-store'

export function LoginScreen(): React.JSX.Element {
  const [baseUrl, setBaseUrl] = useState($baseUrl.get())
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const api = new ApiClient(baseUrl, () => null)
      const token: TokenResponse = await api.login(username, password)
      setAuth(token.access_token, baseUrl, token.user)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-overlay">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1 className="login-title">DeskAgent</h1>
        <input className="login-input" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="http://localhost:8000" />
        <input className="login-input" value={username} onChange={e => setUsername(e.target.value)} placeholder="用户名" autoCapitalize="off" />
        <input className="login-input" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="密码" />
        {error && <div className="login-error">{error}</div>}
        <button className="login-btn" type="submit" disabled={loading || !username || !password}>
          {loading ? '连接中…' : '登录'}
        </button>
      </form>
    </div>
  )
}
