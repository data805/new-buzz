import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link } from 'react-router-dom'
import { PhoneIncoming, PhoneMissed, CheckCircle2, Timer, Radio, Gauge } from 'lucide-react'
import { get, fmtDate, fmtDuration } from '../lib/api'
import { Card, ErrorBox, Notice, PageHeader, Select, Stat, StatusBadge, Table, Td, useLoad } from '../components/ui'
import { useState } from 'react'

interface Overview {
  total: number; answered: number; missed: number; successful: number; inbound: number; outbound: number
  avg_duration_s: number; avg_first_audio_ms: number | null; escalated: number; live: number
  by_day: { date: string; inbound: number; outbound: number; missed: number }[]
  outcomes: Record<string, number>; intents: Record<string, number>
  open: { enquiries: number; tickets: number; callbacks: number }
}
interface Health { [k: string]: { ok?: boolean; detail?: string } | unknown }

export default function OverviewPage() {
  const [days, setDays] = useState(14)
  const { data, error } = useLoad(() => get<Overview>(`/api/stats/overview?days=${days}`), [days])
  const health = useLoad(() => get<Health>('/api/system/status'))
  const recent = useLoad(() => get<{ items: { id: string; direction: string; from_number: string; to_number: string; outcome: string; status: string; created_at: string; duration_s: number; summary: string }[] }>('/api/calls?page_size=8'))
  const knowledge = useLoad(() => get<{ sample_sources_live: number }>('/api/knowledge'))
  const comps = ['llm', 'stt', 'tts', 'knowledge', 'telephony', 'database'] as const
  return (
    <div className="space-y-5">
      <PageHeader title="Overview" sub="Call activity and system health"
        actions={<Select value={days} onChange={(e) => setDays(+e.target.value)} className="w-36">
          {[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </Select>} />
      <ErrorBox error={error} />
      {knowledge.data && knowledge.data.sample_sources_live > 0 && (
        <Notice>
          The knowledge base contains <b>{knowledge.data.sample_sources_live} sample source(s)</b> built from public web content. Review them in{' '}
          <Link className="underline" to="/knowledge">Knowledge</Link> and replace with verified, approved information before taking real calls.
        </Notice>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Total calls" value={data?.total ?? '—'} sub={`${data?.inbound ?? 0} in / ${data?.outbound ?? 0} out`} icon={<PhoneIncoming className="h-4 w-4" />} />
        <Stat label="Answered" value={data?.answered ?? '—'} icon={<CheckCircle2 className="h-4 w-4" />} />
        <Stat label="Missed / failed" value={data?.missed ?? '—'} icon={<PhoneMissed className="h-4 w-4" />} />
        <Stat label="Successful" value={data?.successful ?? '—'} sub={`${data?.escalated ?? 0} escalated`} />
        <Stat label="Avg duration" value={fmtDuration(data?.avg_duration_s)} icon={<Timer className="h-4 w-4" />} />
        <Stat label="Avg response" value={data?.avg_first_audio_ms ? `${(data.avg_first_audio_ms / 1000).toFixed(1)}s` : '—'} sub="caller stops → agent speaks" icon={<Gauge className="h-4 w-4" />} />
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Call volume" className="lg:col-span-2">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data?.by_day ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e7e5e4" />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} fontSize={11} />
                <YAxis allowDecimals={false} fontSize={11} width={30} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="inbound" stackId="a" fill="#d97706" name="Inbound" />
                <Bar dataKey="outbound" stackId="a" fill="#0ea5e9" name="Outbound" />
                <Bar dataKey="missed" fill="#a8a29e" name="Missed" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="System health" actions={<Link to="/system" className="text-xs text-brand-700 hover:underline">Details</Link>}>
          <ul className="space-y-2 text-sm">
            {comps.map((c) => {
              const v = (health.data?.[c] ?? {}) as { ok?: boolean; detail?: string }
              return (
                <li key={c} className="flex items-center justify-between gap-2">
                  <span className="capitalize">{c === 'llm' ? 'Language model' : c === 'stt' ? 'Speech-to-text' : c === 'tts' ? 'Text-to-speech' : c}</span>
                  <span className="flex items-center gap-2 text-xs text-stone-500">
                    <span className="max-w-40 truncate">{v.detail}</span>
                    <span className={`h-2.5 w-2.5 rounded-full ${health.data ? (v.ok ? 'bg-emerald-500' : 'bg-amber-500') : 'bg-stone-300'}`} />
                  </span>
                </li>
              )
            })}
            <li className="flex items-center justify-between border-t border-stone-100 pt-2">
              <span className="flex items-center gap-1.5"><Radio className="h-3.5 w-3.5" /> Live calls</span>
              <Link to="/live" className="font-semibold text-brand-700">{data?.live ?? 0}</Link>
            </li>
          </ul>
        </Card>
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Recent calls" className="lg:col-span-2" pad={false} actions={<Link to="/calls" className="text-xs text-brand-700 hover:underline">All calls</Link>}>
          <Table head={['When', 'Direction', 'Number', 'Outcome', 'Duration', 'Summary']}>
            {recent.data?.items.map((c) => (
              <tr key={c.id} className="hover:bg-stone-50">
                <Td className="whitespace-nowrap"><Link className="text-brand-700 hover:underline" to={`/calls/${c.id}`}>{fmtDate(c.created_at)}</Link></Td>
                <Td className="capitalize">{c.direction}</Td>
                <Td className="whitespace-nowrap">{c.direction === 'inbound' ? c.from_number : c.to_number}</Td>
                <Td><StatusBadge value={c.outcome || c.status} /></Td>
                <Td>{fmtDuration(c.duration_s)}</Td>
                <Td className="max-w-xs truncate text-stone-600">{c.summary}</Td>
              </tr>
            ))}
          </Table>
          {recent.data?.items.length === 0 && <div className="p-6 text-center text-sm text-stone-500">No calls yet. Try the <Link to="/test" className="underline">test console</Link>.</div>}
        </Card>
        <Card title="Open follow-ups">
          <div className="grid grid-cols-3 gap-2 text-center">
            {(['enquiries', 'tickets', 'callbacks'] as const).map((k) => (
              <Link key={k} to={`/records?tab=${k}`} className="rounded-lg bg-stone-50 p-3 hover:bg-brand-50">
                <div className="text-xl font-semibold">{data?.open[k] ?? '—'}</div>
                <div className="text-xs capitalize text-stone-500">{k}</div>
              </Link>
            ))}
          </div>
          <div className="mt-4 text-xs font-medium uppercase text-stone-500">Top intents</div>
          <ul className="mt-2 space-y-1 text-sm">
            {Object.entries(data?.intents ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, v]) => (
              <li key={k} className="flex justify-between"><span className="capitalize">{k.replace(/_/g, ' ')}</span><span className="text-stone-500">{v}</span></li>
            ))}
            {data && Object.keys(data.intents).length === 0 && <li className="text-stone-500">No data yet</li>}
          </ul>
        </Card>
      </div>
    </div>
  )
}
