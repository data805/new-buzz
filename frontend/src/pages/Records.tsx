import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { get, patch, fmtDate } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Card, Empty, ErrorBox, PageHeader, Select, StatusBadge, Table, Tabs, Td, useAction, useLoad } from '../components/ui'

type Row = Record<string, string | number | boolean | null>
const COLS: Record<string, [string, string][]> = {
  enquiries: [['created_at', 'Received'], ['name', 'Name'], ['phone', 'Phone'], ['email', 'Email'], ['topic', 'Topic'], ['team', 'Team'], ['details', 'Details']],
  tickets: [['created_at', 'Opened'], ['priority', 'Priority'], ['subject', 'Subject'], ['team', 'Team'], ['phone', 'Phone'], ['details', 'Details']],
  callbacks: [['created_at', 'Requested'], ['name', 'Name'], ['phone', 'Phone'], ['requested_time', 'Preferred time'], ['reason', 'Reason']],
  feedback: [['created_at', 'Received'], ['rating', 'Rating'], ['comment', 'Comment']],
}
const STATUSES: Record<string, string[]> = { enquiries: ['new', 'contacted', 'closed'], tickets: ['open', 'in_progress', 'resolved'], callbacks: ['pending', 'done', 'cancelled'] }

export default function RecordsPage() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'enquiries'
  const [status, setStatus] = useState('')
  const { can } = useAuth()
  const { data, error, reload } = useLoad(() => get<Row[]>(`/api/records/${tab}${status ? `?status=${status}` : ''}`), [tab, status])
  const { run } = useAction()
  const setRowStatus = async (id: string, s: string) => {
    await run(() => patch(`/api/records/${tab}/${id}`, { status: s }), 'Updated')
    reload()
  }
  return (
    <div className="space-y-4">
      <PageHeader title="Follow-ups" sub="Enquiries, tickets, callbacks and feedback created by calls and workflows" />
      <Tabs value={tab} onChange={(t) => { setSp({ tab: t }); setStatus('') }} tabs={Object.keys(COLS).map((k) => ({ id: k, label: k[0].toUpperCase() + k.slice(1) }))} />
      {STATUSES[tab] && (
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
          <option value="">All statuses</option>{STATUSES[tab].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </Select>
      )}
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={[...COLS[tab].map((c) => c[1]), ...(STATUSES[tab] ? ['Status'] : []), 'Call']}>
          {data?.map((r) => (
            <tr key={String(r.id)}>
              {COLS[tab].map(([k]) => (
                <Td key={k} className={k === 'details' || k === 'reason' || k === 'comment' ? 'max-w-sm whitespace-pre-wrap text-stone-600' : 'whitespace-nowrap'}>
                  {k === 'created_at' ? fmtDate(String(r[k])) : k === 'priority' ? <StatusBadge value={String(r[k])} /> : String(r[k] ?? '') || '—'}
                </Td>
              ))}
              {STATUSES[tab] && (
                <Td>{can('operator') ? (
                  <Select value={String(r.status)} onChange={(e) => setRowStatus(String(r.id), e.target.value)} className="w-32 py-1 text-xs">
                    {STATUSES[tab].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                  </Select>
                ) : <StatusBadge value={String(r.status)} />}</Td>
              )}
              <Td>{r.call_id ? <Link className="text-brand-700 hover:underline" to={`/calls/${r.call_id}`}>View</Link> : '—'}</Td>
            </tr>
          ))}
        </Table>
        {data?.length === 0 && <Empty>Nothing here yet.</Empty>}
      </Card>
    </div>
  )
}
