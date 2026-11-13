import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Eye, EyeOff } from 'lucide-react'
import { useAuth } from '../context/auth'
import { LogoMark } from '../components/Logo'
import type { RoleId } from '../lib/types.ops'

export default function AuthPage() {
  const { user, login, signup, busy, error } = useAuth()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const mode = params.get('mode') === 'signup' ? 'signup' : 'login'
  const authState = location.state as { notice?: unknown; returnTo?: unknown } | null
  const notice = authState?.notice
  const destination = typeof authState?.returnTo === 'string' && authState.returnTo.startsWith('/') && !authState.returnTo.startsWith('//')
    ? authState.returnTo
    : '/map'
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<RoleId>('community')
  const [showPassword, setShowPassword] = useState(false)

  if (user) return <Navigate to={destination} replace />

  const switchMode = (next: 'login' | 'signup') => {
    setParams({ mode: next }, { replace: true })
    setPassword('')
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const success = mode === 'login'
      ? await login(email, password)
      : await signup(name, email, password, role)
    if (success) {
      navigate(destination, { replace: true })
    }
  }

  const fieldClass = 'h-12 w-full rounded-md border border-navy/35 bg-white px-3.5 text-base text-navy placeholder:text-navy/45 focus:border-sign focus:outline-none focus:ring-2 focus:ring-sign/30'

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card-top">
          <Link to="/map" className="auth-back-link"><ArrowLeft size={16} /> Back to map</Link>
        </div>
        <header className="auth-brand">
          <span className="auth-mark"><LogoMark size={40} /></span>
          <span>
            <span className="auth-brand-name">WAYMARK</span>
            <span className="auth-brand-tagline">Road-safety decision support</span>
          </span>
        </header>

        <div className="auth-intro">
          <h1 id="auth-title">{mode === 'login' ? 'Sign in' : 'Create your account'}</h1>
          <p>{mode === 'login' ? 'Use your WAYMARK account to report incidents, review evidence and track measures.' : 'Your role decides what you can report, review and approve.'}</p>
        </div>

        {typeof notice === 'string' && <p role="status" className="auth-hint">{notice}</p>}

        {mode === 'login' ? (
          <form className="auth-form" onSubmit={(event) => void submit(event)}>
            <label className="auth-label" htmlFor="auth-email">Email</label>
            <input
              id="auth-email"
              className={fieldClass}
              type="text"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
              maxLength={254}
              placeholder="Enter your email"
            />
            <label className="auth-label" htmlFor="auth-password">Password</label>
            <div className="auth-password-wrap">
              <input
                id="auth-password"
                className={`${fieldClass} pr-14`}
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
                maxLength={256}
                placeholder="Enter your password"
              />
              <button type="button" className="auth-eye" onClick={() => setShowPassword((shown) => !shown)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
              </button>
            </div>
            <p className="auth-hint">Passwords need 6 or more characters.</p>
            {error && <p role="alert" className="auth-error">{error}</p>}
            <button type="submit" disabled={busy} className="auth-submit">
              {busy ? 'Please wait…' : 'Sign in'}
            </button>
          </form>
        ) : (
          <form className="auth-form" onSubmit={(event) => void submit(event)}>
            <label className="auth-label" htmlFor="signup-name">Name</label>
            <input id="signup-name" className={fieldClass} type="text" value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" required maxLength={100} placeholder="Enter your name" />
            <label className="auth-label" htmlFor="signup-email">Email</label>
            <input id="signup-email" className={fieldClass} type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required maxLength={254} placeholder="Enter your email" />
            <label className="auth-label" htmlFor="signup-password">Password</label>
            <input id="signup-password" className={fieldClass} type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required minLength={6} maxLength={256} placeholder="Create a password" />
            <label className="auth-label" htmlFor="signup-role">Role</label>
            <select id="signup-role" className={fieldClass} value={role} onChange={(event) => setRole(event.target.value as RoleId)} required>
              <option value="engineer">Road Authorities</option>
              <option value="planner">City Planners</option>
              <option value="community">Traffic Police</option>
            </select>
            <p className="auth-hint">Use at least 6 characters. Your role controls workspace permissions.</p>
            {error && <p role="alert" className="auth-error">{error}</p>}
            <button type="submit" disabled={busy} className="auth-submit">
              {busy ? 'Please wait…' : 'Create account'}
            </button>
          </form>
        )}

        <p className="auth-switch">
          {mode === 'login' ? <>No account? <button type="button" onClick={() => switchMode('signup')}>Create one</button></> : <>Already have an account? <button type="button" onClick={() => switchMode('login')}>Sign in</button></>}
        </p>
      </section>
    </main>
  )
}
