import { useState } from 'react'
import { Plus, ShieldCheck, Trash2, FlaskConical, RotateCw, Check, X } from 'lucide-react'
import { del, get, post, put, fmtDate } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Td, Textarea, useAction, useLoad } from '../components/ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Wf { id: string; name: string; description: string; trigger: string; conditions: any[]; actions: any[]; enabled: boolean; approved: boolean; approved_by: string | null; require_run_approval: boolean; template: string | null; has_external: boolean }
interface Meta { events: Record<string, string>; actions: Record<string, { external: boolean; label: string; params: string[] }>; operators: string[]; templates: any[] }
interface Run { id: string; workflow: string; event_type: string; status: string; results: any[]; error: string | null; attempts: number; payload: any; created_at: string }

const SAMPLE_PAYLOAD = {
  call_id: 'test-call', direction: 'inbound', phone: '+260971234567', outcome: 'completed', escalated: false, is_test: true,
  extracted: { summary: 'Caller asked about coworking prices and wants a callback.', intent: 'coworking', team: 'coworking', caller_name: 'Mwila Banda', enquiry_topic: 'Coworking pricing', callback_requested: true, callback_time: 'tomorrow morning', needs_human: false, feedback_rating: null },
}

export default function WorkflowsPage() {
  const [tab, setTab] = useState('workflows')
  const meta = useLoad(() => get<Meta>('/api/workflows/meta'))
  const wfs = useLoad(() => get<Wf[]>('/api/workflows'))
  return (
    <div className="space-y-4">
      <PageHeader title="Workflows" sub="Automations triggered by call events" />
      <Notice tone="blue">Internal actions (enquiries, tickets, callbacks, feedback, notifications) run immediately. Actions that reach outside systems — webhooks, n8n, email, SMS, outbound calls — only run once an <b>admin approves</b> the workflow, never for test-console calls, and every run is de-duplicated by an idempotency key.</Notice>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'workflows', label: 'Workflows' }, { id: 'runs', label: 'Run history' }]} />
      {tab === 'workflows' ? <WorkflowList wfs={wfs.data} meta={meta.data} reload={wfs.reload} error={wfs.error || meta.error} /> : <Runs />}
    </div>
  )
}

function WorkflowList({ wfs, meta, reload, error }: { wfs: Wf[] | null; meta: Meta | null; reload: () => void; error: string | null }) {
  const { can } = useAuth()
  const [editing, setEditing] = useState<Partial<Wf> | null>(null)
  const [testing, setTesting] = useState<Wf | null>(null)
  const { run } = useAction()
  const toggle = async (w: Wf) => { await run(() => put(`/api/workflows/${w.id}`, { ...w, enabled: !w.enabled }), w.enabled ? 'Disabled' : 'Enabled'); reload() }
  const approve = async (w: Wf, yes: boolean) => { await run(() => post(`/api/workflows/${w.id}/${yes ? 'approve' : 'revoke'}`), yes ? 'Approved' : 'Approval revoked'); reload() }
  const remove = async (w: Wf) => { if (confirm(`Delete workflow "${w.name}"?`)) { await run(() => del(`/api/workflows/${w.id}`), 'Deleted'); reload() } }
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      {can('manager') && <Button variant="primary" onClick={() => setEditing({ name: '', description: '', trigger: 'call.completed', conditions: [], actions: [{ type: 'create_ticket', params: {} }], enabled: false, require_run_approval: false })}><Plus className="h-4 w-4" /> New workflow</Button>}
      <div className="grid gap-3 lg:grid-cols-2">
        {wfs?.map((w) => (
          <Card key={w.id} title={<span className="flex items-center gap-2">{w.name}</span>}
            actions={<>
              {w.has_external && (w.approved ? <Badge color="green">approved</Badge> : <Badge color="amber">needs approval</Badge>)}
              <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={w.enabled} disabled={!can('manager')} onChange={() => toggle(w)} /> enabled</label>
            </>}>
            <p className="text-sm text-stone-600">{w.description}</p>
            <div className="mt-3 space-y-1 text-xs">
              <div><span className="font-semibold">When</span> <Badge color="blue">{w.trigger}</Badge></div>
              {w.conditions.length > 0 && <div><span className="font-semibold">If</span> {w.conditions.map((c, i) => <Badge key={i}>{c.field} {c.op} {c.value ?? ''}</Badge>)}</div>}
              <div><span className="font-semibold">Then</span> {w.actions.map((a, i) => <Badge key={i} color={meta?.actions[a.type]?.external ? 'violet' : 'gray'}>{meta?.actions[a.type]?.label ?? a.type}</Badge>)}</div>
              {w.require_run_approval && <div className="text-amber-700">Each run waits for manual approval.</div>}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setTesting(w)}><FlaskConical className="h-3.5 w-3.5" /> Dry run</Button>
              {can('manager') && <Button size="sm" onClick={() => setEditing(w)}>Edit</Button>}
              {can('admin') && w.has_external && (w.approved
                ? <Button size="sm" onClick={() => approve(w, false)}>Revoke approval</Button>
                : <Button size="sm" variant="primary" onClick={() => approve(w, true)}><ShieldCheck className="h-3.5 w-3.5" /> Approve external actions</Button>)}
              {can('manager') && <Button size="sm" variant="ghost" onClick={() => remove(w)}><Trash2 className="h-3.5 w-3.5 text-red-600" /></Button>}
            </div>
          </Card>
        ))}
      </div>
      {wfs?.length === 0 && <Empty>No workflows.</Empty>}
      {editing && meta && <WorkflowEditor initial={editing} meta={meta} onClose={() => setEditing(null)} onDone={reload} />}
      {testing && <DryRun wf={testing} onClose={() => setTesting(null)} />}
    </div>
  )
}

function WorkflowEditor({ initial, meta, onClose, onDone }: { initial: Partial<Wf>; meta: Meta; onClose: () => void; onDone: () => void }) {
  const [w, setW] = useState<any>(structuredClone(initial))
  const [paramsText, setParamsText] = useState<string[]>(w.actions.map((a: any) => JSON.stringify(a.params ?? {}, null, 2)))
  const { busy, run } = useAction()
  const fromTemplate = (id: string) => {
    const t = meta.templates.find((x) => x.template === id)
    if (!t) return
    setW({ ...w, name: t.name, description: t.description, trigger: t.trigger, conditions: t.conditions, actions: t.actions, require_run_approval: !!t.require_run_approval })
    setParamsText(t.actions.map((a: any) => JSON.stringify(a.params ?? {}, null, 2)))
  }
  const save = async () => {
    let actions
    try {
      actions = w.actions.map((a: any, i: number) => ({ ...a, params: JSON.parse(paramsText[i] || '{}') }))
    } catch {
      alert('Action parameters must be valid JSON'); return
    }
    const body = { name: w.name, description: w.description, trigger: w.trigger, conditions: w.conditions, actions, enabled: w.enabled, require_run_approval: w.require_run_approval }
    if (await run(() => (w.id ? put(`/api/workflows/${w.id}`, body) : post('/api/workflows', body)), 'Workflow saved')) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title={w.id ? 'Edit workflow' : 'New workflow'} wide>
      <div className="space-y-4">
        {!w.id && <Field label="Start from template"><Select defaultValue="" onChange={(e) => fromTemplate(e.target.value)}><option value="">— blank —</option>{meta.templates.map((t) => <option key={t.template} value={t.template}>{t.name}</option>)}</Select></Field>}
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name"><Input value={w.name} onChange={(e) => setW({ ...w, name: e.target.value })} /></Field>
          <Field label="Trigger event"><Select value={w.trigger} onChange={(e) => setW({ ...w, trigger: e.target.value })}>{Object.entries(meta.events).map(([k, v]) => <option key={k} value={k}>{k} — {v}</option>)}</Select></Field>
        </div>
        <Field label="Description"><Input value={w.description} onChange={(e) => setW({ ...w, description: e.target.value })} /></Field>
        <div>
          <div className="mb-1 text-xs font-medium text-stone-600">Conditions (all must match). Fields use dot paths into the event payload, e.g. extracted.intent</div>
          {w.conditions.map((c: any, i: number) => (
            <div key={i} className="mb-2 grid grid-cols-[1fr_8rem_1fr_auto] gap-2">
              <Input value={c.field} placeholder="extracted.intent" onChange={(e) => { const cs = [...w.conditions]; cs[i] = { ...c, field: e.target.value }; setW({ ...w, conditions: cs }) }} />
              <Select value={c.op} onChange={(e) => { const cs = [...w.conditions]; cs[i] = { ...c, op: e.target.value }; setW({ ...w, conditions: cs }) }}>{meta.operators.map((o) => <option key={o}>{o}</option>)}</Select>
              <Input value={c.value ?? ''} placeholder="value" onChange={(e) => { const cs = [...w.conditions]; cs[i] = { ...c, value: e.target.value }; setW({ ...w, conditions: cs }) }} />
              <Button size="sm" variant="ghost" onClick={() => setW({ ...w, conditions: w.conditions.filter((_: any, j: number) => j !== i) })}><X className="h-3.5 w-3.5" /></Button>
            </div>
          ))}
          <Button size="sm" onClick={() => setW({ ...w, conditions: [...w.conditions, { field: '', op: 'exists' }] })}><Plus className="h-3.5 w-3.5" /> Condition</Button>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-stone-600">Actions (run in order). Parameters accept {'{{placeholders}}'} such as {'{{phone}}'} or {'{{extracted.caller_name}}'}.</div>
          {w.actions.map((a: any, i: number) => (
            <div key={i} className="mb-2 rounded-lg border border-stone-200 p-3">
              <div className="mb-2 flex gap-2">
                <Select value={a.type} onChange={(e) => { const as = [...w.actions]; as[i] = { ...a, type: e.target.value }; setW({ ...w, actions: as }) }}>
                  {Object.entries(meta.actions).map(([k, v]) => <option key={k} value={k}>{v.label}{v.external ? ' (external)' : ''}</option>)}
                </Select>
                <label className="flex shrink-0 items-center gap-1 text-xs"><input type="checkbox" checked={!!a.continue_on_error} onChange={(e) => { const as = [...w.actions]; as[i] = { ...a, continue_on_error: e.target.checked }; setW({ ...w, actions: as }) }} /> continue on error</label>
                <Button size="sm" variant="ghost" onClick={() => { setW({ ...w, actions: w.actions.filter((_: any, j: number) => j !== i) }); setParamsText(paramsText.filter((_, j) => j !== i)) }}><X className="h-3.5 w-3.5" /></Button>
              </div>
              <Textarea rows={4} className="font-mono text-xs" value={paramsText[i] ?? '{}'} onChange={(e) => setParamsText(paramsText.map((p, j) => (j === i ? e.target.value : p)))} />
              <div className="mt-1 text-xs text-stone-500">Parameters: {meta.actions[a.type]?.params.join(', ')}</div>
            </div>
          ))}
          <Button size="sm" onClick={() => { setW({ ...w, actions: [...w.actions, { type: 'notify_staff', params: {} }] }); setParamsText([...paramsText, '{\n  "message": ""\n}']) }}><Plus className="h-3.5 w-3.5" /> Action</Button>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={w.enabled} onChange={(e) => setW({ ...w, enabled: e.target.checked })} /> Enabled</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={w.require_run_approval} onChange={(e) => setW({ ...w, require_run_approval: e.target.checked })} /> Require manual approval for each run</label>
        </div>
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={save} disabled={!w.name || w.actions.length === 0}>Save</Button></div>
      </div>
    </Modal>
  )
}

function DryRun({ wf, onClose }: { wf: Wf; onClose: () => void }) {
  const [payload, setPayload] = useState(JSON.stringify(SAMPLE_PAYLOAD, null, 2))
  const [res, setRes] = useState<any>(null)
  const { busy, run } = useAction()
  const test = async () => {
    let p
    try { p = JSON.parse(payload) } catch { alert('Invalid JSON'); return }
    setRes(await run(() => post(`/api/workflows/${wf.id}/test`, { payload: p })))
  }
  return (
    <Modal open onClose={onClose} title={`Dry run: ${wf.name}`} wide>
      <div className="space-y-3">
        <p className="text-sm text-stone-600">Evaluates conditions and renders action parameters against a sample event. Nothing is executed.</p>
        <Textarea rows={12} className="font-mono text-xs" value={payload} onChange={(e) => setPayload(e.target.value)} />
        <Button variant="primary" loading={busy} onClick={test}>Evaluate</Button>
        {res && <pre className="overflow-x-auto rounded-lg bg-stone-900 p-3 text-xs text-stone-100">{JSON.stringify(res, null, 2)}</pre>}
      </div>
    </Modal>
  )
}

function Runs() {
  const { can } = useAuth()
  const [status, setStatus] = useState('')
  const { data, error, reload } = useLoad(() => get<Run[]>(`/api/workflows/runs/list${status ? `?status=${status}` : ''}`), [status])
  const { run } = useAction()
  const act = async (r: Run, a: string) => { await run(() => post(`/api/workflows/runs/${r.id}/${a}`), `Run ${a}`); reload() }
  const [open, setOpen] = useState<Run | null>(null)
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-52">
          <option value="">All runs</option>{['success', 'failed', 'awaiting_approval', 'running', 'rejected'].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </Select>
        <Button onClick={reload}><RotateCw className="h-4 w-4" /> Refresh</Button>
      </div>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['When', 'Workflow', 'Event', 'Status', 'Steps', 'Error', '']}>
          {data?.map((r) => (
            <tr key={r.id} className="hover:bg-stone-50">
              <Td className="whitespace-nowrap"><button className="text-brand-700 hover:underline" onClick={() => setOpen(r)}>{fmtDate(r.created_at)}</button></Td>
              <Td>{r.workflow}</Td><Td><Badge color="blue">{r.event_type}</Badge></Td><Td><StatusBadge value={r.status} /></Td>
              <Td className="text-xs">{r.results.map((x, i) => <span key={i} className="mr-1">{x.action}:{x.status}</span>)}</Td>
              <Td className="max-w-xs truncate text-xs text-red-600">{r.error}</Td>
              <Td className="whitespace-nowrap">{can('manager') && <>
                {r.status === 'awaiting_approval' && <><Button size="sm" onClick={() => act(r, 'approve')}><Check className="h-3.5 w-3.5" /> Approve</Button> <Button size="sm" variant="ghost" onClick={() => act(r, 'reject')}>Reject</Button></>}
                {r.status === 'failed' && <Button size="sm" onClick={() => act(r, 'retry')}><RotateCw className="h-3.5 w-3.5" /> Retry</Button>}
              </>}</Td>
            </tr>
          ))}
        </Table>
        {data?.length === 0 && <Empty>No workflow runs yet.</Empty>}
      </Card>
      {open && <Modal open onClose={() => setOpen(null)} title="Run details" wide><pre className="overflow-x-auto rounded-lg bg-stone-900 p-3 text-xs text-stone-100">{JSON.stringify(open, null, 2)}</pre></Modal>}
    </div>
  )
}
