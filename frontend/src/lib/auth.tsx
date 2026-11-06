import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { get, post } from './api'

export type Role = 'viewer' | 'operator' | 'manager' | 'admin'
export interface User {
  id: string
  email: string
  name: string
  role: Role
  must_change_password: boolean
  auth_disabled?: boolean
}

const ORDER: Role[] = ['viewer', 'operator', 'manager', 'admin']

interface AuthCtx {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  can: (role: Role) => boolean
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setUser(await get<User>('/api/auth/me'))
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const onExpired = () => setUser(null)
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [refresh])

  const login = async (email: string, password: string) => {
    setUser(await post<User>('/api/auth/login', { email, password }))
  }
  const logout = async () => {
    await post('/api/auth/logout')
    setUser(null)
  }
  const can = (role: Role) => !!user && ORDER.indexOf(user.role) >= ORDER.indexOf(role)

  return <Ctx.Provider value={{ user, loading, login, logout, refresh, can }}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
