import { useState } from 'react'
import { KeyRound, UserPlus, Trash2, Eraser } from 'lucide-react'
import { del, get, patch, post, fmtDate } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Table, Tabs, Td, useAction, useLoad } from '../components/ui'

interface U { id: string; email: string; name: string; role: string; active: boolean; last_login_at: string | null }

export default function AdminPage() {
  const [tab, setTab] = useState('users')
  return (
    <div className="space-y-4">
      <PageHeader title="Security & access" sub="Users, roles, API keys, audit trail and privacy requests" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'users', label: 'Users & roles' }, { id: 'keys', label: 'API keys' }, { id: 'audit', label: 'Audit log' }, { id: 'privacy', label: 'Privacy' }]} />
      {tab === 'users' && <Users />}
      {tab === 'keys' && <Keys />}
      {tab === 'audit' && <Audit />}
      {tab === 'privacy' && <Privacy />}
    </div>
  )
}

function Users() {
  const { user: me } = useAuth()
  const { data, error, reload } = useLoad(() => get<U[]>('/api/users'))
  const roles = useLoad(() => get<{ role: string; description: string }[]>('/api/roles'))
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', role: 'operator', password: '' })
  const { busy, run } = useAction()
  const create = async () => { if (await run(() => post('/api/users', form), 'User created — they must change the password at first login')) { setAdding(false); reload() } }
  const update = async (u: U, body: Partial<U> & { password?: string }) => { await run(() => patch(`/api/users/${u.id}`, body), 'Updated'); reload() }
  const reset = async (u: U) => {
    const pw = prompt(`Temporary password for ${u.email} (10+ chars, mixed case, digit):`)
    if (pw) await update(u, { password: pw })
  }
  return (
    <div className="space-y-3">
      <Button variant="primary" onClick={() => setAdding(true)}><UserPlus className="h-4 w-4" /> Add user</Button>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['User', 'Role', 'Status', 'Last login', '']}>
          {data?.map((u) => (
            <tr key={u.id}>
              <Td><div className="font-medium">{u.name || u.email}</div><div className="text-xs text-stone-500">{u.email}</div></Td>
              <Td><Select value={u.role} disabled={u.id === me?.id} onChange={(e) => update(u, { role: e.target.value })} className="w-32 py-1 text-xs">{['viewer', 'operator', 'manager', 'admin'].map((r) => <option key={r}>{r}</option>)}</Select></Td>
              <Td>{u.active ? <Badge color="green">active</Badge> : <Badge color="red">disabled</Badge>}</Td>
              <Td className="text-xs">{fmtDate(u.last_login_at)}</Td>
              <Td className="whitespace-nowrap">
                <Button size="sm" variant="ghost" onClick={() => reset(u)}>Reset password</Button>
                {u.id !== me?.id && <Button size="sm" variant="ghost" onClick={() => update(u, { active: !u.active })}>{u.active ? 'Disable' : 'Enable'}</Button>}
              </Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Roles">
        <ul className="space-y-1 text-sm">{roles.data?.map((r) => <li key={r.role}><b className="capitalize">{r.role}</b> — {r.description}</li>)}</ul>
      </Card>
      <Modal open={adding} onClose={() => setAdding(false)} title="Add user">
        <div className="space-y-3">
          <Field label="Email"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Role"><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{['viewer', 'operator', 'manager', 'admin'].map((r) => <option key={r}>{r}</option>)}</Select></Field>
          <Field label="Temporary password" hint="10+ characters, mixed case and a digit. The user must change it at first login."><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <div className="flex justify-end gap-2"><Button onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" loading={busy} onClick={create}>Create</Button></div>
        </div>
      </Modal>
    </div>
  )
}

function Keys() {
  const { data, error, reload } = useLoad(() => get<{ scopes: string[]; keys: { id: string; name: string; prefix: string; scopes: string[]; revoked: boolean; created_at: string; last_used_at: string | null }[] }>('/api/api-keys'))
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>(['events:create'])
  const [created, setCreated] = useState<string | null>(null)
  const { busy, run } = useAction()
  const create = async () => {
    const r = await run(() => post<{ key: string }>('/api/api-keys', { name, scopes }))
    if (r) { setCreated(r.key); setName(''); reload() }
  }
  const revoke = async (id: string) => { if (confirm('Revoke this key? Integrations using it will stop working.')) { await run(() => del(`/api/api-keys/${id}`), 'Revoked'); reload() } }
  return (
    <div className="space-y-3">
      <Card title="Create an API key">
        <p className="mb-3 text-sm text-stone-600">For machine integrations such as n8n. Send it as the <code>X-API-Key</code> header to <code>/api/v1/*</code>. See docs/API.md.</p>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="n8n production" /></Field>
          <div className="md:col-span-2">
            <div className="mb-1 text-xs font-medium text-stone-600">Scopes</div>
            <div className="flex flex-wrap gap-3 text-sm">{data?.scopes.map((s) => (
              <label key={s} className="flex items-center gap-1"><input type="checkbox" checked={scopes.includes(s)} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />{s}</label>
            ))}</div>
          </div>
        </div>
        <Button className="mt-3" variant="primary" loading={busy} onClick={create} disabled={!name || !scopes.length}><KeyRound className="h-4 w-4" /> Create key</Button>
        {created && <Notice><div className="font-medium">Copy this key now — it will not be shown again:</div><code className="mt-1 block break-all font-mono text-xs">{created}</code></Notice>}
      </Card>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['Name', 'Prefix', 'Scopes', 'Created', 'Last used', '']}>
          {data?.keys.map((k) => (
            <tr key={k.id} className={k.revoked ? 'opacity-50' : ''}>
              <Td>{k.name}</Td><Td className="font-mono text-xs">{k.prefix}…</Td><Td>{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</Td>
              <Td className="text-xs">{fmtDate(k.created_at)}</Td><Td className="text-xs">{fmtDate(k.last_used_at)}</Td>
              <Td>{k.revoked ? <Badge color="red">revoked</Badge> : <Button size="sm" variant="ghost" onClick={() => revoke(k.id)}><Trash2 className="h-3.5 w-3.5 text-red-600" /></Button>}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  )
}

function Audit() {
  const [action, setAction] = useState('')
  const { data, error } = useLoad(() => get<{ id: number; ts: string; actor: string; action: string; target: string; details: Record<string, unknown>; ip: string }[]>(`/api/audit${action ? `?action=${action}` : ''}`), [action])
  return (
    <div className="space-y-3">
      <Input placeholder="Filter by action prefix (e.g. campaign, knowledge, auth)" value={action} onChange={(e) => setAction(e.target.value)} className="max-w-sm" />
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['When', 'Actor', 'Action', 'Target', 'Details', 'IP']}>
          {data?.map((a) => (
            <tr key={a.id}><Td className="whitespace-nowrap text-xs">{fmtDate(a.ts)}</Td><Td className="text-xs">{a.actor}</Td><Td><Badge>{a.action}</Badge></Td>
              <Td className="text-xs">{a.target}</Td><Td className="max-w-xs truncate font-mono text-xs text-stone-500">{JSON.stringify(a.details)}</Td><Td className="text-xs">{a.ip}</Td></tr>
          ))}
        </Table>
      </Card>
    </div>
  )
}

function Privacy() {
  const [phone, setPhone] = useState('')
  const { busy, run } = useAction()
  const erase = async () => {
    if (!confirm(`Erase all transcripts, recordings and contact details for ${phone}? The number will be kept on the do-not-call list. This cannot be undone.`)) return
    const r = await run(() => post<{ calls_erased: number }>('/api/privacy/erase', { phone }))
    if (r) alert(`Erased data for ${r.calls_erased} call(s).`)
  }
  const retention = async () => {
    const r = await run(() => post<Record<string, number>>('/api/system/retention/run'))
    if (r) alert(`Retention applied: ${JSON.stringify(r)}`)
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Data subject erasure">
        <p className="mb-3 text-sm text-stone-600">Handles a person's request to delete their data (Data Protection Act No. 3 of 2021). Removes transcripts, recordings, summaries, enquiries, callbacks and tickets linked to the number, and keeps a do-not-call marker so they are never called again.</p>
        <div className="flex gap-2"><Input placeholder="+260 97…" value={phone} onChange={(e) => setPhone(e.target.value)} /><Button variant="danger" loading={busy} onClick={erase} disabled={!phone}><Eraser className="h-4 w-4" /> Erase</Button></div>
      </Card>
      <Card title="Retention">
        <p className="mb-3 text-sm text-stone-600">Transcripts and recordings older than the periods set in Agent settings → Recording &amp; privacy are deleted automatically every 6 hours. Run it now:</p>
        <Button onClick={retention} loading={busy}>Apply retention now</Button>
      </Card>
    </div>
  )
}
