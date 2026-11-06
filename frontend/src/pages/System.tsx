import { useState } from 'react'
import { RotateCw } from 'lucide-react'
import { get } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, ErrorBox, Input, PageHeader, Select, Stat, Table, Tabs, Td, useLoad } from '../components/ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function SystemPage() {
  const { can } = useAuth()
  const [tab, setTab] = useState('status')
  return (
    <div className="space-y-4">
      <PageHeader title="System" sub="Health, performance, resources and logs" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'status', label: 'Status & integrations' }, { id: 'metrics', label: 'Performance & resources' }, ...(can('manager') ? [{ id: 'logs', label: 'Logs' }] : [])]} />
      {tab === 'status' && <Status />}
      {tab === 'metrics' && <Metrics />}
      {tab === 'logs' && <Logs />}
    </div>
  )
}

function Dot({ ok }: { ok: boolean | undefined }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-amber-500'}`} />
}

function Status() {
  const { data, error, reload, loading } = useLoad(() => get<any>('/api/system/status'))
  const tel = useLoad(() => get<any>('/api/telephony/status'))
  const rows: [string, string][] = [['database', 'Database'], ['llm', 'Language model'], ['stt', 'Speech-to-text'], ['tts', 'Text-to-speech'], ['knowledge', 'Knowledge index'], ['telephony', 'Telephony']]
  return (
    <div className="space-y-4">
      <Button onClick={() => { reload(); tel.reload() }} loading={loading}><RotateCw className="h-4 w-4" /> Refresh</Button>
      <ErrorBox error={error} />
      {data?.production_problems?.length > 0 && <ErrorBox error={`Production configuration problems: ${data.production_problems.join('; ')}`} />}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Components" pad={false}>
          <Table head={['Component', 'Status', 'Detail']}>
            {rows.map(([k, label]) => (
              <tr key={k}><Td>{label}</Td><Td><Dot ok={data?.[k]?.ok} /></Td>
                <Td className="text-xs text-stone-600">{data && Object.entries(data[k] ?? {}).filter(([kk]) => kk !== 'ok').map(([kk, v]) => <div key={kk}><span className="text-stone-400">{kk}:</span> {Array.isArray(v) ? v.join(', ') || '—' : String(v)}</div>)}</Td></tr>
            ))}
          </Table>
        </Card>
        <div className="space-y-4">
          <Card title="Telephony">
            {tel.data && <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-stone-500">Provider</dt><dd><Badge color={tel.data.provider === 'asterisk' ? 'green' : 'amber'}>{tel.data.provider}</Badge></dd>
              <dt className="text-stone-500">Outbound caller ID</dt><dd>{tel.data.outbound_caller_id || '—'}</dd>
              <dt className="text-stone-500">SIP trunk endpoint</dt><dd>{tel.data.trunk_endpoint} ({tel.data.dial_format})</dd>
              <dt className="text-stone-500">Human transfer target</dt><dd>{tel.data.human_transfer_target ? 'configured' : 'not set'}</dd>
              <dt className="text-stone-500">Allowed countries</dt><dd>+{tel.data.allowed_country_codes.join(', +')}</dd>
            </dl>}
            {tel.data?.provider === 'simulator' && <p className="mt-3 text-xs text-stone-500">Running on the local simulator. To take real calls on a Zambian number, follow docs/TELEPHONY_ZAMBIA.md and set TELEPHONY_PROVIDER=asterisk.</p>}
          </Card>
          <Card title="Capacity & outbound">
            {data && <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-stone-500">Concurrent calls</dt><dd>{data.capacity.active} / {data.capacity.max_concurrent_calls}</dd>
              <dt className="text-stone-500">Outbound calling</dt><dd><Badge color={data.dialer.outbound_enabled ? 'green' : 'gray'}>{data.dialer.outbound_enabled ? 'enabled' : 'disabled'}</Badge></dd>
              <dt className="text-stone-500">Dialer last tick</dt><dd className="text-xs">{data.dialer.last_tick ? new Date(data.dialer.last_tick).toLocaleTimeString() : '—'}</dd>
              {data.dialer.last_error && <><dt className="text-stone-500">Dialer error</dt><dd className="text-xs text-red-600">{data.dialer.last_error}</dd></>}
            </dl>}
          </Card>
          <Card title="Integrations">
            {data && <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-stone-500">Email (SMTP)</dt><dd><Dot ok={data.integrations.smtp} /> {data.integrations.smtp ? 'configured' : 'not configured'}</dd>
              <dt className="text-stone-500">SMS</dt><dd><Dot ok={!!data.integrations.sms} /> {data.integrations.sms || 'not configured'}</dd>
              <dt className="text-stone-500">n8n</dt><dd><Dot ok={!!data.integrations.n8n} /> {data.integrations.n8n || 'not configured'}</dd>
              <dt className="text-stone-500">Webhook hosts</dt><dd className="text-xs">{data.integrations.webhook_allowed_hosts.join(', ') || 'none allowed'}</dd>
            </dl>}
          </Card>
        </div>
      </div>
    </div>
  )
}

function Metrics() {
  const [hours, setHours] = useState(24)
  const { data, error, reload } = useLoad(() => get<any>(`/api/system/metrics?hours=${hours}`), [hours])
  const sys = data?.system
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Select value={hours} onChange={(e) => setHours(+e.target.value)} className="w-40">{[1, 24, 168, 720].map((h) => <option key={h} value={h}>Last {h < 48 ? `${h}h` : `${h / 24} days`}</option>)}</Select>
        <Button onClick={reload}><RotateCw className="h-4 w-4" /> Refresh</Button>
      </div>
      <ErrorBox error={error} />
      {sys && <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="CPU" value={`${sys.cpu_percent}%`} sub={`${sys.cpu_count} cores · load ${sys.load_avg.join(' / ')}`} />
        <Stat label="Memory" value={`${sys.memory.used_percent}%`} sub={`process ${sys.memory.process_rss_mb} MB of ${Math.round(sys.memory.total_mb / 1024)} GB`} />
        <Stat label="Disk free" value={`${sys.disk.free_gb} GB`} sub={`${sys.disk.used_percent}% used`} />
        <Stat label="Uptime" value={`${Math.floor(sys.uptime_s / 3600)}h ${Math.floor((sys.uptime_s % 3600) / 60)}m`} sub={sys.gpu?.cuda_devices ? `${sys.gpu.cuda_devices} CUDA GPU(s)` : 'CPU inference'} />
      </div>}
      <Card title={`Voice pipeline latency (${data?.calls_measured ?? 0} calls)`} pad={false}>
        <Table head={['Stage', 'Average', 'Median', 'p95', 'Calls']}>
          {Object.entries(data?.latency ?? {}).map(([k, v]: [string, any]) => (
            <tr key={k}><Td>{({ stt_ms: 'Speech-to-text', llm_first_token_ms: 'LLM first token', first_audio_ms: 'Caller stops → agent audio', tts_ms: 'TTS per turn', turn_ms: 'Turn response' } as Record<string, string>)[k] ?? k}</Td>
              <Td className="font-mono">{v.avg} ms</Td><Td className="font-mono">{v.p50} ms</Td><Td className="font-mono">{v.p95} ms</Td><Td>{v.calls}</Td></tr>
          ))}
        </Table>
        {data && Object.keys(data.latency).length === 0 && <div className="p-4 text-sm text-stone-500">No answered calls in this period.</div>}
      </Card>
    </div>
  )
}

function Logs() {
  const [level, setLevel] = useState('INFO')
  const [q, setQ] = useState('')
  const { data, error, reload } = useLoad(() => get<any[]>(`/api/system/logs?level=${level}${q ? `&q=${encodeURIComponent(q)}` : ''}`), [level, q])
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Select value={level} onChange={(e) => setLevel(e.target.value)} className="w-36">{['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => <option key={l}>{l}</option>)}</Select>
        <Input placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <Button onClick={reload}><RotateCw className="h-4 w-4" /> Refresh</Button>
      </div>
      <ErrorBox error={error} />
      <Card pad={false}>
        <div className="max-h-[65vh] overflow-auto font-mono text-xs scroll-thin">
          {data?.map((l, i) => (
            <div key={i} className={`border-b border-stone-100 px-3 py-1.5 ${l.level === 'ERROR' || l.level === 'CRITICAL' ? 'bg-red-50' : l.level === 'WARNING' ? 'bg-amber-50' : ''}`}>
              <span className="text-stone-400">{l.ts}</span> <span className="font-semibold">{l.level}</span> <span className="text-stone-500">{l.logger}</span> {l.msg}
              {Object.entries(l).filter(([k]) => !['ts', 'level', 'logger', 'msg', 'exc'].includes(k)).map(([k, v]) => <span key={k} className="ml-2 text-sky-700">{k}={String(v)}</span>)}
              {l.exc && <pre className="mt-1 whitespace-pre-wrap text-red-700">{l.exc}</pre>}
            </div>
          ))}
          {data?.length === 0 && <div className="p-4 text-stone-500">No log entries.</div>}
        </div>
      </Card>
    </div>
  )
}
