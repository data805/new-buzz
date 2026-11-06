export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail.map((d) => (typeof d === 'object' && d && 'msg' in d ? `${(d as { loc?: string[] }).loc?.slice(-1)[0] ?? ''}: ${(d as { msg: string }).msg}` : String(d))).join('; ')
  return 'Request failed'
}

export async function api<T = unknown>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = opts
  const res = await fetch(path, {
    credentials: 'same-origin',
    ...rest,
    headers: { ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new Event('auth:expired'))
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      msg = detailToMessage(body.detail) || msg
    } catch { /* not json */ }
    throw new ApiError(res.status, msg)
  }
  const ct = res.headers.get('content-type') || ''
  return (ct.includes('application/json') ? res.json() : res.blob()) as Promise<T>
}

export const get = <T,>(p: string) => api<T>(p)
export const post = <T,>(p: string, json?: unknown) => api<T>(p, { method: 'POST', json: json ?? {} })
export const put = <T,>(p: string, json: unknown) => api<T>(p, { method: 'PUT', json })
export const patch = <T,>(p: string, json: unknown) => api<T>(p, { method: 'PATCH', json })
export const del = <T,>(p: string) => api<T>(p, { method: 'DELETE' })

export function upload<T>(p: string, form: FormData) {
  return api<T>(p, { method: 'POST', body: form })
}

export function wsUrl(path: string) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}`
}

export function fmtDate(s?: string | null) {
  if (!s) return '—'
  const d = new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z')
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function fmtDuration(sec?: number | null) {
  if (!sec) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m ? `${m}m ${s}s` : `${s}s`
}
