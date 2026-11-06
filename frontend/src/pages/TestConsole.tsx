import { useEffect, useRef, useState } from 'react'
import { Mic, MicOff, Phone, PhoneOff, Send, Smartphone, PhoneIncoming } from 'lucide-react'
import { wsUrl } from '../lib/api'
import { VoiceClient, type VoiceEvent } from '../lib/voice'
import { Badge, Button, Card, Field, Input, Notice, PageHeader, Select, Tabs, cx } from '../components/ui'

interface Line { role: 'user' | 'assistant' | 'event'; text: string; meta?: string }

export default function TestConsolePage() {
  const [tab, setTab] = useState('call')
  return (
    <div className="space-y-4">
      <PageHeader title="Test console" sub="Talk to the agent from your browser before connecting a phone line" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'call', label: 'Browser call (inbound)' }, { id: 'phone', label: 'Simulated phone (outbound)' }]} />
      {tab === 'call' ? <BrowserCall /> : <SimPhone />}
    </div>
  )
}

function useCallLog() {
  const [lines, setLines] = useState<Line[]>([])
  const [state, setState] = useState('idle')
  const [sources, setSources] = useState<{ title: string; heading: string; score: number }[]>([])
  const [latency, setLatency] = useState<Record<string, number> | null>(null)
  const handle = (e: VoiceEvent) => {
    switch (e.type) {
      case 'state': setState(String(e.state)); break
      case 'transcript': setLines((l) => [...l, { role: 'user', text: String(e.text) }]); break
      case 'assistant':
        setLines((l) => {
          const last = l[l.length - 1]
          if (last?.role === 'assistant' && last.meta === String(e.turn)) return [...l.slice(0, -1), { ...last, text: `${last.text} ${e.text}` }]
          return [...l, { role: 'assistant', text: String(e.text), meta: String(e.turn) }]
        })
        break
      case 'sources': setSources(e.items as typeof sources); break
      case 'metrics': setLatency(e.latency as Record<string, number>); break
      case 'interrupted': setLines((l) => [...l, { role: 'event', text: 'caller interrupted the agent' }]); break
      case 'call_ended': setLines((l) => [...l, { role: 'event', text: `call ended: ${e.outcome}` }]); setState('ended'); break
      case 'error': setLines((l) => [...l, { role: 'event', text: `error: ${e.message}` }]); break
    }
  }
  return { lines, setLines, state, setState, sources, latency, handle }
}

function Transcript({ lines }: { lines: Line[] }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [lines])
  return (
    <div className="h-96 space-y-2 overflow-y-auto rounded-lg bg-stone-50 p-3 scroll-thin">
      {lines.length === 0 && <div className="pt-20 text-center text-sm text-stone-400">The conversation will appear here.</div>}
      {lines.map((l, i) => l.role === 'event'
        ? <div key={i} className="text-center text-xs text-stone-400">— {l.text} —</div>
        : (
          <div key={i} className={cx('flex', l.role === 'user' ? 'justify-start' : 'justify-end')}>
            <div className={cx('max-w-[85%] rounded-xl px-3 py-2 text-sm', l.role === 'user' ? 'bg-white shadow-sm' : 'bg-brand-600 text-white')}>{l.text}</div>
          </div>
        ))}
      <div ref={end} />
    </div>
  )
}

function Meter({ level, label }: { level: number; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-stone-500">
      <span className="w-14">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded bg-stone-200"><div className="h-full bg-brand-500 transition-all" style={{ width: `${Math.min(100, level * 140)}%` }} /></div>
    </div>
  )
}

function SidePanel({ state, sources, latency }: { state: string; sources: { title: string; heading: string; score: number }[]; latency: Record<string, number> | null }) {
  return (
    <div className="space-y-4">
      <Card title="Agent state"><Badge color={state === 'speaking' ? 'violet' : state === 'thinking' ? 'amber' : state === 'listening' ? 'green' : 'gray'}>{state}</Badge></Card>
      <Card title="Last turn latency">
        {latency ? (
          <dl className="grid grid-cols-2 gap-1 text-sm">
            {Object.entries(latency).map(([k, v]) => <div key={k} className="contents"><dt className="text-stone-500">{k.replace(/_ms$/, '').replace(/_/g, ' ')}</dt><dd className="text-right font-mono">{v} ms</dd></div>)}
          </dl>
        ) : <div className="text-sm text-stone-400">—</div>}
      </Card>
      <Card title="Knowledge used">
        {sources.length ? <ul className="space-y-1 text-xs">{sources.map((s, i) => <li key={i}><b>{s.title}</b>{s.heading && ` › ${s.heading}`} <span className="text-stone-400">({s.score})</span></li>)}</ul> : <div className="text-sm text-stone-400">No passages above the relevance threshold.</div>}
      </Card>
    </div>
  )
}

function BrowserCall() {
  const client = useRef<VoiceClient | null>(null)
  const log = useCallLog()
  const [connected, setConnected] = useState(false)
  const [muted, setMuted] = useState(false)
  const [levels, setLevels] = useState([0, 0])
  const [text, setText] = useState('')
  const [mode, setMode] = useState<'test' | 'simulate'>('test')
  const [caller, setCaller] = useState('+260971234567')
  const [useMic, setUseMic] = useState(true)

  const start = async () => {
    log.setLines([])
    const params = new URLSearchParams({ mode, ...(mode === 'simulate' ? { caller } : {}) })
    const c = new VoiceClient(wsUrl(`/ws/voice?${params}`))
    client.current = c
    c.onEvent = (e) => {
      log.handle(e)
      if (e.type === 'socket_closed' || e.type === 'call_ended' || e.type === 'hangup') { setConnected(false) }
    }
    c.onLevel = (m, s) => setLevels([m, s])
    c.ws.onopen = async () => {
      setConnected(true)
      log.setState('starting')
      try {
        await c.startAudio(useMic)
      } catch (err) {
        log.setLines((l) => [...l, { role: 'event', text: `microphone unavailable (${(err as Error).message}); use typed input` }])
        await c.startAudio(false)
      }
    }
  }
  const stop = () => { client.current?.hangup(); setTimeout(() => client.current?.close(), 1500); setConnected(false) }
  useEffect(() => () => client.current?.close(), [])
  const sendText = () => { if (text.trim()) { client.current?.sendText(text.trim()); setText('') } }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card>
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <Field label="Mode" hint={mode === 'test' ? 'Test call: external workflow actions are skipped.' : 'Simulated phone call: behaves like a real inbound call from this number.'}>
              <Select value={mode} disabled={connected} onChange={(e) => setMode(e.target.value as 'test' | 'simulate')}><option value="test">Test call</option><option value="simulate">Simulated inbound call</option></Select>
            </Field>
            {mode === 'simulate' && <Field label="Caller number"><Input value={caller} disabled={connected} onChange={(e) => setCaller(e.target.value)} /></Field>}
            <label className="mt-6 flex items-center gap-2 text-sm"><input type="checkbox" checked={useMic} disabled={connected} onChange={(e) => setUseMic(e.target.checked)} /> Use microphone</label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!connected
              ? <Button variant="primary" onClick={start}><Phone className="h-4 w-4" /> Start call</Button>
              : <Button variant="danger" onClick={stop}><PhoneOff className="h-4 w-4" /> Hang up</Button>}
            {connected && useMic && <Button onClick={() => { const m = !muted; setMuted(m); if (client.current) client.current.muted = m }}>{muted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}{muted ? 'Unmute' : 'Mute'}</Button>}
            <div className="min-w-48 flex-1 space-y-1"><Meter level={levels[0]} label="You" /><Meter level={levels[1]} label="Agent" /></div>
          </div>
        </Card>
        <Transcript lines={log.lines} />
        <div className="flex gap-2">
          <Input placeholder={connected ? 'Type instead of speaking…' : 'Start a call to type messages'} value={text} disabled={!connected} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendText()} />
          <Button onClick={sendText} disabled={!connected}><Send className="h-4 w-4" /></Button>
        </div>
        <Notice tone="blue">Use headphones for the most realistic test; speakers can feed the agent's voice back into the mic. You can interrupt the agent by talking over it.</Notice>
      </div>
      <SidePanel state={log.state} sources={log.sources} latency={log.latency} />
    </div>
  )
}

function SimPhone() {
  const [number, setNumber] = useState('+260971234567')
  const [status, setStatus] = useState<'off' | 'registered' | 'ringing' | 'in_call'>('off')
  const [incoming, setIncoming] = useState<{ call_id: string; from: string } | null>(null)
  const client = useRef<VoiceClient | null>(null)
  const log = useCallLog()
  const [levels, setLevels] = useState([0, 0])
  const register = () => {
    const c = new VoiceClient(wsUrl(`/ws/simphone?number=${encodeURIComponent(number)}`))
    client.current = c
    c.onLevel = (m, s) => setLevels([m, s])
    c.onEvent = async (e) => {
      log.handle(e)
      if (e.type === 'registered') setStatus('registered')
      if (e.type === 'incoming') { setIncoming({ call_id: String(e.call_id), from: String(e.from) }); setStatus('ringing') }
      if (e.type === 'missed') { setIncoming(null); setStatus('registered'); log.setLines((l) => [...l, { role: 'event', text: 'missed call' }]) }
      if (e.type === 'call_ended') { setStatus('registered') }
      if (e.type === 'socket_closed') { setStatus('off') }
    }
  }
  const answer = async () => {
    setIncoming(null)
    log.setLines([])
    try { await client.current?.startAudio(true) } catch { await client.current?.startAudio(false) }
    client.current?.send({ type: 'answer' })
    setStatus('in_call')
  }
  const decline = () => { client.current?.send({ type: 'decline' }); setIncoming(null); setStatus('registered') }
  useEffect(() => () => client.current?.close(), [])
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card>
          <p className="mb-3 text-sm text-stone-600">Register this browser as a phone on the simulated network, then trigger an outbound call to the number (Outbound → Single call, a campaign, or a workflow). Ignore the call to test "no answer" handling, or decline it to test "busy". Requires TELEPHONY_PROVIDER=simulator and OUTBOUND_ENABLED=true; the number must be a contact with consent.</p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="This phone's number"><Input value={number} disabled={status !== 'off'} onChange={(e) => setNumber(e.target.value)} /></Field>
            {status === 'off'
              ? <Button variant="primary" onClick={register}><Smartphone className="h-4 w-4" /> Go online</Button>
              : <Button onClick={() => { client.current?.close(); setStatus('off') }}>Go offline</Button>}
            {status === 'in_call' && <Button variant="danger" onClick={() => client.current?.hangup()}><PhoneOff className="h-4 w-4" /> Hang up</Button>}
            <Badge color={status === 'off' ? 'gray' : status === 'ringing' ? 'amber' : 'green'}>{status.replace('_', ' ')}</Badge>
          </div>
          {incoming && (
            <div className="mt-4 flex items-center justify-between rounded-xl border-2 border-brand-500 bg-brand-50 p-4">
              <div className="flex items-center gap-3"><PhoneIncoming className="h-6 w-6 animate-pulse text-brand-700" /><div><div className="font-semibold">Incoming call</div><div className="text-sm text-stone-600">from {incoming.from}</div></div></div>
              <div className="flex gap-2"><Button variant="primary" onClick={answer}>Answer</Button><Button variant="danger" onClick={decline}>Decline</Button></div>
            </div>
          )}
          {status === 'in_call' && <div className="mt-3 space-y-1"><Meter level={levels[0]} label="You" /><Meter level={levels[1]} label="Agent" /></div>}
        </Card>
        <Transcript lines={log.lines} />
      </div>
      <SidePanel state={log.state} sources={log.sources} latency={log.latency} />
    </div>
  )
}
