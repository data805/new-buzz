import { useRef, useState } from 'react'
import { Play, Pause, Send, ShieldCheck, Upload, UserPlus, Ban, PhoneOutgoing, Plus, XCircle } from 'lucide-react'
import { get, post, put, upload, fmtDate } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Td, Textarea, useAction, useLoad } from '../components/ui'

interface Contact { id: string; phone: string; name: string; email: string; organisation: string; consent_status: string; consent_source: string; opted_out_at: string | null; operator: string; created_at: string }
interface Campaign {
  id: string; name: string; purpose: string; script: string; opening_line: string; status: string; created_by: string; approved_by: string | null
  max_concurrent: number; calls_per_hour: number; max_attempts: number; retry_delay_minutes: number; window_start: string; window_end: string; days: number[]
  voicemail_action: string; voicemail_message: string; stats: Record<string, number>; counts: Record<string, number>; in_window_now: boolean
  contacts?: { id: number; phone: string; name: string; consent_status: string; status: string; attempts: number; last_outcome: string; last_call_id: string | null; next_attempt_at: string | null }[]
}
interface CampList { outbound_enabled: boolean; allow_bulk_outbound: boolean; bulk_threshold: number; items: Campaign[] }

export default function OutboundPage() {
  const [tab, setTab] = useState('campaigns')
  const camps = useLoad(() => get<CampList>('/api/campaigns'))
  return (
    <div className="space-y-4">
      <PageHeader title="Outbound calling" sub="Approved campaigns to consented contacts only" />
      {camps.data && !camps.data.outbound_enabled && (
        <Notice>Outbound calling is <b>disabled</b> on this server (OUTBOUND_ENABLED=false). You can prepare and approve campaigns; calls will not be placed until an administrator enables outbound calling after the compliance review in docs/COMPLIANCE.md.</Notice>
      )}
      {camps.data && <div className="flex flex-wrap gap-2 text-xs"><Badge color={camps.data.allow_bulk_outbound ? 'amber' : 'green'}>bulk campaigns (&gt;{camps.data.bulk_threshold} contacts): {camps.data.allow_bulk_outbound ? 'allowed' : 'blocked'}</Badge><Badge>consent required for every contact</Badge><Badge>opt-outs honoured immediately</Badge></div>}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'campaigns', label: 'Campaigns' }, { id: 'contacts', label: 'Contacts & consent' }, { id: 'single', label: 'Single call' }]} />
      {tab === 'campaigns' && <Campaigns data={camps.data} error={camps.error} reload={camps.reload} />}
      {tab === 'contacts' && <Contacts />}
      {tab === 'single' && <SingleCall />}
    </div>
  )
}

const EMPTY: Partial<Campaign> = {
  name: '', purpose: 'follow_up', script: '', opening_line: '', max_concurrent: 1, calls_per_hour: 10, max_attempts: 3, retry_delay_minutes: 60,
  window_start: '09:00', window_end: '17:00', days: [0, 1, 2, 3, 4], voicemail_action: 'hangup', voicemail_message: '',
}

function Campaigns({ data, error, reload }: { data: CampList | null; error: string | null; reload: () => void }) {
  const { can } = useAuth()
  const [editing, setEditing] = useState<Partial<Campaign> | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  return (
    <div className="space-y-3">
      {can('manager') && <Button variant="primary" onClick={() => setEditing({ ...EMPTY })}><Plus className="h-4 w-4" /> New campaign</Button>}
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['Campaign', 'Purpose', 'Status', 'Contacts', 'Progress', 'Window', 'Created']}>
          {data?.items.map((c) => (
            <tr key={c.id} className="cursor-pointer hover:bg-stone-50" onClick={() => setViewing(c.id)}>
              <Td className="font-medium text-brand-700">{c.name}</Td>
              <Td className="capitalize">{c.purpose.replace(/_/g, ' ')}</Td>
              <Td><StatusBadge value={c.status} /></Td>
              <Td>{c.counts.total ?? 0}</Td>
              <Td className="text-xs">{Object.entries(c.counts).filter(([k]) => k !== 'total').map(([k, v]) => `${k.replace('_', ' ')}: ${v}`).join(' · ')}</Td>
              <Td className="whitespace-nowrap text-xs">{c.window_start}–{c.window_end} {c.in_window_now ? <Badge color="green">open</Badge> : <Badge>closed</Badge>}</Td>
              <Td className="text-xs text-stone-500">{c.created_by}</Td>
            </tr>
          ))}
        </Table>
        {data?.items.length === 0 && <Empty>No campaigns yet.</Empty>}
      </Card>
      {editing && <CampaignForm initial={editing} onClose={() => setEditing(null)} onDone={reload} />}
      {viewing && <CampaignView id={viewing} onClose={() => setViewing(null)} onEdit={(c) => { setViewing(null); setEditing(c) }} onChanged={reload} />}
    </div>
  )
}

function CampaignForm({ initial, onClose, onDone }: { initial: Partial<Campaign>; onClose: () => void; onDone: () => void }) {
  const [c, setC] = useState(initial)
  const { busy, run } = useAction()
  const f = (k: keyof Campaign, v: unknown) => setC({ ...c, [k]: v })
  const save = async () => {
    const body = { ...c }
    delete body.id; delete body.status; delete body.stats; delete body.counts; delete body.contacts; delete body.created_by; delete body.approved_by; delete body.in_window_now
    const r = await run(() => (initial.id ? put(`/api/campaigns/${initial.id}`, body) : post('/api/campaigns', body)), initial.id ? 'Campaign updated (re-approval required)' : 'Campaign created as draft')
    if (r) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title={initial.id ? 'Edit campaign' : 'New campaign'} wide>
      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name"><Input value={c.name} onChange={(e) => f('name', e.target.value)} /></Field>
          <Field label="Purpose"><Select value={c.purpose} onChange={(e) => f('purpose', e.target.value)}>
            {['follow_up', 'appointment_confirmation', 'reminder', 'feedback', 'enquiry_response', 'event_invitation'].map((p) => <option key={p} value={p}>{p.replace(/_/g, ' ')}</option>)}
          </Select></Field>
        </div>
        <Field label="Call purpose & script for the agent" hint="What the agent should accomplish, key facts and questions. The agent still only states facts from approved knowledge.">
          <Textarea rows={6} value={c.script} onChange={(e) => f('script', e.target.value)} placeholder="Example: Confirm the person's attendance at the AI training session they registered for. Ask if they still plan to attend; if not, ask whether they'd like to be told about the next session." />
        </Field>
        <Field label="Opening line (optional)" hint="Must identify BongoHive and that this is an AI assistant."><Input value={c.opening_line} onChange={(e) => f('opening_line', e.target.value)} /></Field>
        <div className="grid gap-3 md:grid-cols-4">
          <Field label="Calls at once"><Input type="number" min={1} value={c.max_concurrent} onChange={(e) => f('max_concurrent', +e.target.value)} /></Field>
          <Field label="Calls per hour"><Input type="number" min={1} value={c.calls_per_hour} onChange={(e) => f('calls_per_hour', +e.target.value)} /></Field>
          <Field label="Max attempts"><Input type="number" min={1} max={5} value={c.max_attempts} onChange={(e) => f('max_attempts', +e.target.value)} /></Field>
          <Field label="Retry delay (min)" hint="multiplied by attempt number"><Input type="number" min={5} value={c.retry_delay_minutes} onChange={(e) => f('retry_delay_minutes', +e.target.value)} /></Field>
          <Field label="Window start (Lusaka)"><Input type="time" value={c.window_start} onChange={(e) => f('window_start', e.target.value)} /></Field>
          <Field label="Window end"><Input type="time" value={c.window_end} onChange={(e) => f('window_end', e.target.value)} /></Field>
          <Field label="Voicemail"><Select value={c.voicemail_action} onChange={(e) => f('voicemail_action', e.target.value)}><option value="hangup">Hang up (retry later)</option><option value="leave_message">Leave a message</option></Select></Field>
        </div>
        <div className="flex flex-wrap gap-3 text-sm">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => (
          <label key={d} className="flex items-center gap-1"><input type="checkbox" checked={c.days?.includes(i)} onChange={(e) => f('days', e.target.checked ? [...(c.days ?? []), i].sort() : (c.days ?? []).filter((x) => x !== i))} />{d}</label>
        ))}</div>
        {c.voicemail_action === 'leave_message' && <Field label="Voicemail message"><Textarea rows={2} value={c.voicemail_message} onChange={(e) => f('voicemail_message', e.target.value)} /></Field>}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save} disabled={!c.name}>Save draft</Button></div>
      </div>
    </Modal>
  )
}

function CampaignView({ id, onClose, onEdit, onChanged }: { id: string; onClose: () => void; onEdit: (c: Campaign) => void; onChanged: () => void }) {
  const { can, user } = useAuth()
  const { data, error, reload } = useLoad(() => get<Campaign>(`/api/campaigns/${id}`), [id])
  const { busy, run } = useAction()
  const file = useRef<HTMLInputElement>(null)
  const [attest, setAttest] = useState('')
  const act = async (a: string) => {
    if (await run(() => post(`/api/campaigns/${id}/${a}`), `Campaign: ${a}`)) { reload(); onChanged() }
  }
  const importCsv = async () => {
    const f = file.current?.files?.[0]
    if (!f) return
    const fd = new FormData(); fd.append('file', f); fd.append('campaign_id', id); fd.append('consent_attestation', attest)
    const r = await run(() => upload<{ imported: number; added_to_campaign: number; errors: { row: number; error: string }[] }>('/api/contacts/import', fd))
    if (r) { alert(`Imported ${r.imported}, added ${r.added_to_campaign} to campaign.${r.errors.length ? `\n${r.errors.length} error(s):\n` + r.errors.slice(0, 10).map((e) => `row ${e.row}: ${e.error}`).join('\n') : ''}`); reload(); onChanged() }
  }
  return (
    <Modal open onClose={onClose} title={data?.name ?? 'Campaign'} wide>
      <ErrorBox error={error} />
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={data.status} />
            {data.approved_by && <span className="text-xs text-stone-500">approved by {data.approved_by}</span>}
            <div className="ml-auto flex flex-wrap gap-2">
              {can('manager') && ['draft', 'paused'].includes(data.status) && <Button size="sm" onClick={() => onEdit(data)}>Edit</Button>}
              {can('manager') && data.status === 'draft' && <Button size="sm" onClick={() => act('submit')} loading={busy}><Send className="h-3.5 w-3.5" /> Submit for approval</Button>}
              {can('admin') && data.status === 'pending_approval' && <>
                <Button size="sm" variant="primary" onClick={() => act('approve')} loading={busy}><ShieldCheck className="h-3.5 w-3.5" /> Approve</Button>
                <Button size="sm" onClick={() => act('reject')}>Reject</Button></>}
              {can('manager') && ['approved', 'paused'].includes(data.status) && <Button size="sm" variant="primary" onClick={() => act('start')} loading={busy}><Play className="h-3.5 w-3.5" /> Start</Button>}
              {data.status === 'running' && <Button size="sm" onClick={() => act('pause')}><Pause className="h-3.5 w-3.5" /> Pause</Button>}
              {can('manager') && !['completed', 'cancelled'].includes(data.status) && <Button size="sm" variant="danger" onClick={() => confirm('Cancel this campaign?') && act('cancel')}><XCircle className="h-3.5 w-3.5" /> Cancel</Button>}
            </div>
          </div>
          {data.status === 'pending_approval' && data.created_by === user?.email && user?.role !== 'admin' && <Notice tone="blue">Waiting for an administrator to approve.</Notice>}
          <div className="rounded-lg bg-stone-50 p-3 text-sm">
            <div className="mb-1 text-xs font-semibold uppercase text-stone-500">Script preview</div>
            <div className="mb-2 italic">“{data.opening_line || 'Hello, this is Buzz, an AI assistant calling on behalf of BongoHive in Lusaka. Is this a good time to talk?'}”</div>
            <div className="whitespace-pre-wrap text-stone-700">{data.script || <span className="text-red-600">No script yet</span>}</div>
          </div>
          <div className="text-xs text-stone-500">{data.calls_per_hour}/hour · {data.max_concurrent} at once · {data.max_attempts} attempts · retry after {data.retry_delay_minutes} min × attempt · {data.window_start}–{data.window_end} · voicemail: {data.voicemail_action}</div>
          {can('manager') && ['draft', 'pending_approval'].includes(data.status) && (
            <Card title="Add contacts from CSV">
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="CSV file" hint="Columns: phone, name, email, organisation, consent (yes/no), consent_source"><input ref={file} type="file" accept=".csv" className="text-sm" /></Field>
                <Field label="Consent attestation" hint="How and when these people agreed to be called (required for consent=yes)."><Input value={attest} onChange={(e) => setAttest(e.target.value)} placeholder="e.g. Opted in on event registration form, Oct 2026" /></Field>
                <div className="flex items-end"><Button onClick={importCsv} loading={busy}><Upload className="h-4 w-4" /> Import</Button></div>
              </div>
            </Card>
          )}
          <Table head={['Phone', 'Name', 'Consent', 'Status', 'Attempts', 'Last outcome', 'Next attempt']}>
            {data.contacts?.map((cc) => (
              <tr key={cc.id}>
                <Td className="whitespace-nowrap">{cc.phone}</Td><Td>{cc.name}</Td><Td><StatusBadge value={cc.consent_status} /></Td><Td><StatusBadge value={cc.status} /></Td>
                <Td>{cc.attempts}</Td><Td className="text-xs">{cc.last_outcome}</Td><Td className="text-xs">{fmtDate(cc.next_attempt_at)}</Td>
              </tr>
            ))}
          </Table>
          {data.contacts?.length === 0 && <Empty>No contacts in this campaign.</Empty>}
        </div>
      )}
    </Modal>
  )
}

function Contacts() {
  const { can } = useAuth()
  const [q, setQ] = useState('')
  const { data, error, reload } = useLoad(() => get<Contact[]>(`/api/contacts${q ? `?q=${encodeURIComponent(q)}` : ''}`), [q])
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ phone: '', name: '', email: '', organisation: '', consent_status: 'unknown', consent_source: '' })
  const { busy, run } = useAction()
  const add = async () => { if (await run(() => post('/api/contacts', form), 'Contact saved')) { setAdding(false); reload() } }
  const optOut = async (c: Contact) => { if (confirm(`Mark ${c.phone} as do-not-call?`)) { await run(() => post(`/api/contacts/${c.id}/opt-out`), 'Opted out'); reload() } }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        {can('manager') && <Button onClick={() => setAdding(true)}><UserPlus className="h-4 w-4" /> Add contact</Button>}
      </div>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['Phone', 'Network', 'Name', 'Organisation', 'Consent', 'Source', '']}>
          {data?.map((c) => (
            <tr key={c.id}>
              <Td className="whitespace-nowrap">{c.phone}</Td><Td>{c.operator}</Td><Td>{c.name}</Td><Td>{c.organisation}</Td>
              <Td><StatusBadge value={c.consent_status} /></Td><Td className="max-w-xs truncate text-xs text-stone-500">{c.consent_source}</Td>
              <Td>{can('operator') && c.consent_status !== 'revoked' && <Button size="sm" variant="ghost" onClick={() => optOut(c)}><Ban className="h-3.5 w-3.5 text-red-600" /> Opt out</Button>}</Td>
            </tr>
          ))}
        </Table>
        {data?.length === 0 && <Empty>No contacts.</Empty>}
      </Card>
      <Modal open={adding} onClose={() => setAdding(false)} title="Add contact">
        <div className="space-y-3">
          <Field label="Phone" hint="Zambian numbers may be entered as 097… or +260 97…"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Organisation"><Input value={form.organisation} onChange={(e) => setForm({ ...form, organisation: e.target.value })} /></Field>
          </div>
          <Field label="Email"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Consent to be called"><Select value={form.consent_status} onChange={(e) => setForm({ ...form, consent_status: e.target.value })}><option value="unknown">Unknown / not given</option><option value="granted">Granted</option><option value="revoked">Do not call</option></Select></Field>
          {form.consent_status === 'granted' && <Field label="How was consent obtained?"><Input value={form.consent_source} onChange={(e) => setForm({ ...form, consent_source: e.target.value })} placeholder="e.g. Verbal consent on inbound call 27 Oct 2026" /></Field>}
          <div className="flex justify-end gap-2"><Button onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" loading={busy} onClick={add}>Save</Button></div>
        </div>
      </Modal>
    </div>
  )
}

function SingleCall() {
  const { can } = useAuth()
  const [phone, setPhone] = useState('')
  const [purpose, setPurpose] = useState('')
  const { busy, run } = useAction()
  const call = async () => {
    const r = await run(() => post<{ call_id: string }>('/api/calls/outbound', { phone, purpose }))
    if (r) alert(`Call queued (id ${r.call_id}). Track it in Live calls / Call history.`)
  }
  return (
    <Card title="Place one authorised call">
      <div className="max-w-xl space-y-3">
        <p className="text-sm text-stone-600">For one-off follow-ups such as confirming an appointment. The number must be a contact with recorded consent; all outbound safeguards apply.</p>
        <Field label="Phone"><Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+260 97 1234567" /></Field>
        <Field label="Purpose (instructions for the agent)"><Textarea rows={3} value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="Confirm the visitor's coworking tour booked for Friday at 10am." /></Field>
        <Button variant="primary" loading={busy} onClick={call} disabled={!can('manager') || !phone || purpose.length < 3}><PhoneOutgoing className="h-4 w-4" /> Call now</Button>
        <p className="text-xs text-stone-500">Tip: with the simulator provider, open Test console → Simulated phone and register this number to receive the call in your browser.</p>
      </div>
    </Card>
  )
}
