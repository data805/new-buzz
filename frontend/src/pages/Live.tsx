import { useEffect, useRef, useState } from 'react'
import { Radio } from 'lucide-react'
import { wsUrl } from '../lib/api'
import { Badge, Card, Empty, PageHeader } from '../components/ui'

interface LiveCall {
  id: string; direction: string; channel: string; from_number: string; to_number: string; state: string; started_at: string; campaign?: string
  transcript: { role: string; text: string }[]
}

export default function LivePage() {
  const [calls, setCalls] = useState<Record<string, LiveCall>>({})
  const [connected, setConnected] = useState(false)
  const [, setTick] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  useEffect(() => {
    let closed = false
    let retry: number | undefined
    const connect = () => {
      const ws = new WebSocket(wsUrl('/ws/live'))
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = window.setTimeout(connect, 3000)
      }
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data)
        setCalls((prev) => {
          const next = { ...prev }
          if (msg.type === 'snapshot') {
            const all: Record<string, LiveCall> = {}
            for (const c of msg.calls) all[c.id] = c
            return all
          }
          if (msg.type === 'call') next[msg.call.id] = { ...(next[msg.call.id] ?? { transcript: [] }), ...msg.call }
          if (msg.type === 'transcript' && next[msg.call_id]) next[msg.call_id] = { ...next[msg.call_id], transcript: [...next[msg.call_id].transcript, { role: msg.role, text: msg.text }].slice(-30) }
          if (msg.type === 'ended') delete next[msg.call_id]
          return next
        })
      }
    }
    connect()
    const t = setInterval(() => setTick((x) => x + 1), 1000)
    return () => {
      closed = true
      clearTimeout(retry)
      clearInterval(t)
      wsRef.current?.close()
    }
  }, [])
  const list = Object.values(calls)
  return (
    <div>
      <PageHeader title="Live calls" sub="Calls currently handled by the agent, updated in real time"
        actions={<Badge color={connected ? 'green' : 'red'}>{connected ? 'connected' : 'reconnecting…'}</Badge>} />
      {list.length === 0 ? (
        <Card><Empty><Radio className="mx-auto mb-2 h-6 w-6 text-stone-400" />No active calls right now.</Empty></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((c) => {
            const secs = Math.max(0, Math.round((Date.now() - new Date(c.started_at).getTime()) / 1000))
            return (
              <Card key={c.id} title={<span className="flex items-center gap-2">{c.direction === 'inbound' ? c.from_number : c.to_number}<Badge color="blue">{c.channel}</Badge></span>}
                actions={<><Badge color={c.state === 'speaking' ? 'violet' : c.state === 'thinking' ? 'amber' : 'green'}>{c.state}</Badge><span className="font-mono text-xs text-stone-500">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</span></>}>
                <div className="mb-2 text-xs text-stone-500">{c.direction}{c.campaign ? ` · campaign: ${c.campaign}` : ''}</div>
                <div className="max-h-72 space-y-2 overflow-y-auto text-sm scroll-thin">
                  {c.transcript.map((t, i) => (
                    <div key={i} className={t.role === 'user' ? 'text-stone-800' : 'text-brand-700'}>
                      <span className="mr-1 text-xs font-semibold uppercase">{t.role === 'user' ? 'Caller' : 'Agent'}</span>{t.text}
                    </div>
                  ))}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
