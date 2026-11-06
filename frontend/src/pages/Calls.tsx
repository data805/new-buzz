import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Download, Trash2 } from 'lucide-react'
import { del, get, fmtDate, fmtDuration } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, Empty, ErrorBox, Input, Modal, PageHeader, Select, StatusBadge, Table, Td, useAction, useLoad } from '../components/ui'

interface CallRow {
  id: string; direction: string; channel: string; from_number: string; to_number: string; status: string; outcome: string; hangup_cause: string
  created_at: string; duration_s: number; turn_count: number; summary: string; intent: string; sentiment: string; resolved: boolean | null
  has_recording: boolean; metrics: Record<string, { avg: number; p95: number; n: number }>
}
interface CallDetail extends CallRow {
  action_items: string[]; extracted: Record<string, unknown>; error: string | null
  turns: { id: number; ts: string; role: string; text: string; interrupted: boolean; latency: Record<string, number>; sources: { title: string; heading: string; score: number; sample: boolean }[] }[]
}

const OUTCOMES = ['completed', 'transferred', 'escalated', 'no_answer', 'busy', 'voicemail', 'failed', 'abandoned', 'opted_out', 'rejected']

export default function CallsPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState({ direction: '', outcome: '', channel: '', date_from: '', date_to: '' })
  const [page, setPage] = useState(1)
  const params = new URLSearchParams({ page: String(page), page_size: '25', ...(q ? { q } : {}), ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) })
  const { data, error, loading } = useLoad(() => get<{ total: number; items: CallRow[] }>(`/api/calls?${params}`), [params.toString()])
  useEffect(() => { setPage(1) }, [q, filters])
  const pages = data ? Math.max(1, Math.ceil(data.total / 25)) : 1
  return (
    <div className="space-y-4">
      <PageHeader title="Call history" sub={data ? `${data.total} calls` : ''} />
      <Card>
        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Input placeholder="Search number, summary, id…" value={q} onChange={(e) => setQ(e.target.value)} className="lg:col-span-2" />
          <Select value={filters.direction} onChange={(e) => setFilters({ ...filters, direction: e.target.value })}>
            <option value="">All directions</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option>
          </Select>
          <Select value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}>
            <option value="">All outcomes</option>{OUTCOMES.map((o) => <option key={o} value={o}>{o.replace('_', ' ')}</option>)}
          </Select>
          <Input type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} title="From" />
          <Input type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} title="To" />
        </div>
      </Card>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['Started', 'Direction', 'Channel', 'From', 'To', 'Outcome', 'Duration', 'Turns', 'Intent', 'Summary']}>
          {data?.items.map((c) => (
            <tr key={c.id} className="cursor-pointer hover:bg-stone-50" onClick={() => nav(`/calls/${c.id}`)}>
              <Td className="whitespace-nowrap text-brand-700">{fmtDate(c.created_at)}</Td>
              <Td className="capitalize">{c.direction}</Td>
              <Td><Badge>{c.channel}</Badge></Td>
              <Td className="whitespace-nowrap">{c.from_number || '—'}</Td>
              <Td className="whitespace-nowrap">{c.to_number || '—'}</Td>
              <Td><StatusBadge value={c.outcome || c.status} /></Td>
              <Td>{fmtDuration(c.duration_s)}</Td>
              <Td>{c.turn_count}</Td>
              <Td className="capitalize">{c.intent.replace(/_/g, ' ')}</Td>
              <Td className="max-w-sm truncate text-stone-600">{c.summary}</Td>
            </tr>
          ))}
        </Table>
        {!loading && data?.items.length === 0 && <Empty>No calls match these filters.</Empty>}
        <div className="flex items-center justify-between border-t border-stone-100 px-4 py-2 text-sm">
          <span className="text-stone-500">Page {page} of {pages}</span>
          <div className="flex gap-2">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      </Card>
      {id && <CallDetailModal id={id} onClose={() => nav('/calls')} />}
    </div>
  )
}

function CallDetailModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { can } = useAuth()
  const { data, error } = useLoad(() => get<CallDetail>(`/api/calls/${id}`), [id])
  const { busy, run } = useAction()
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const loadRecording = async () => {
    const blob = await run(() => get<Blob>(`/api/calls/${id}/recording`))
    if (blob) setAudioUrl(URL.createObjectURL(blob))
  }
  const remove = async () => {
    if (!confirm('Permanently delete this call, its transcript and recording?')) return
    if (await run(() => del(`/api/calls/${id}`), 'Call deleted')) onClose()
  }
  const ex = (data?.extracted ?? {}) as Record<string, unknown>
  return (
    <Modal open onClose={onClose} title="Call details" wide>
      <ErrorBox error={error} />
      {data && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <Info k="Direction" v={<span className="capitalize">{data.direction} · {data.channel}</span>} />
            <Info k="Outcome" v={<StatusBadge value={data.outcome || data.status} />} />
            <Info k="From → To" v={`${data.from_number || '—'} → ${data.to_number || '—'}`} />
            <Info k="Duration" v={fmtDuration(data.duration_s)} />
            <Info k="Started" v={fmtDate(data.created_at)} />
            <Info k="Intent" v={<span className="capitalize">{data.intent.replace(/_/g, ' ') || '—'}</span>} />
            <Info k="Sentiment" v={data.sentiment || '—'} />
            <Info k="Resolved" v={data.resolved == null ? '—' : data.resolved ? 'Yes' : 'No'} />
          </div>
          {data.summary && <div className="rounded-lg bg-stone-50 p-3 text-sm"><div className="mb-1 text-xs font-semibold uppercase text-stone-500">Summary</div>{data.summary}</div>}
          {data.action_items?.length > 0 && (
            <div className="text-sm"><div className="mb-1 text-xs font-semibold uppercase text-stone-500">Action items</div>
              <ul className="list-disc pl-5">{data.action_items.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
          )}
          {Object.keys(ex).length > 0 && (
            <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-3">
              {['caller_name', 'caller_phone', 'caller_email', 'organisation', 'enquiry_topic', 'callback_time', 'team', 'feedback_rating'].filter((k) => ex[k]).map((k) => (
                <Info key={k} k={k.replace(/_/g, ' ')} v={String(ex[k])} />
              ))}
            </div>
          )}
          {Object.keys(data.metrics || {}).length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {Object.entries(data.metrics).map(([k, v]) => <Badge key={k} color="blue">{k.replace(/_/g, ' ')}: avg {v.avg} ms · p95 {v.p95} ms</Badge>)}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {data.has_recording && can('manager') && !audioUrl && <Button size="sm" onClick={loadRecording} loading={busy}><Download className="h-3.5 w-3.5" /> Load recording</Button>}
            {audioUrl && <audio controls src={audioUrl} className="w-full" />}
            {can('admin') && <Button size="sm" variant="danger" onClick={remove}><Trash2 className="h-3.5 w-3.5" /> Delete call</Button>}
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold uppercase text-stone-500">Transcript</div>
            <div className="space-y-3">
              {data.turns.map((t) => (
                <div key={t.id} className={`flex ${t.role === 'user' ? 'justify-start' : 'justify-end'}`}>
                  <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${t.role === 'user' ? 'bg-stone-100' : 'bg-brand-50 text-stone-800'}`}>
                    <div className="mb-0.5 text-[11px] font-semibold uppercase text-stone-500">{t.role === 'user' ? 'Caller' : 'Agent'} · {new Date(t.ts + (t.ts.includes('+') || t.ts.endsWith('Z') ? '' : 'Z')).toLocaleTimeString()}{t.interrupted && ' · interrupted'}</div>
                    {t.text}
                    {t.sources?.length > 0 && <div className="mt-1 text-[11px] text-stone-500">Sources: {t.sources.map((s) => `${s.title}${s.heading ? ' › ' + s.heading : ''}`).join('; ')}</div>}
                    {t.latency?.first_audio_ms ? <div className="mt-0.5 text-[11px] text-stone-400">STT {t.latency.stt_ms} ms · LLM first token {t.latency.llm_first_token_ms} ms · first audio {t.latency.first_audio_ms} ms</div> : null}
                  </div>
                </div>
              ))}
              {data.turns.length === 0 && <div className="text-sm text-stone-500">No transcript (expired, erased or call not connected{data.hangup_cause ? `: ${data.hangup_cause}` : ''}).</div>}
            </div>
          </div>
          <div className="text-xs text-stone-400">Call ID {data.id} · <Link to={`/calls?q=${data.id}`} className="underline" onClick={onClose}>permalink</Link></div>
        </div>
      )}
    </Modal>
  )
}

function Info({ k, v }: { k: string; v: React.ReactNode }) {
  return <div><div className="text-xs capitalize text-stone-500">{k}</div><div className="font-medium">{v}</div></div>
}
