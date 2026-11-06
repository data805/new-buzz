import { useRef, useState } from 'react'
import { CheckCircle2, Archive, FileUp, Globe, MessageSquarePlus, Search, Trash2, Pencil, Plus } from 'lucide-react'
import { del, get, patch, post, upload, fmtDate } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Table, Td, Textarea, useAction, useLoad } from '../components/ui'

interface Source {
  id: string; title: string; kind: string; filename: string | null; url: string | null; category: string; tags: string[]; status: string
  is_sample: boolean; chunk_count: number; ingest_status: string; error: string | null; created_by: string; approved_by: string | null
  updated_at: string; chars: number; content?: string
}
interface ListResp { items: Source[]; indexed_chunks: number; sample_sources_live: number; supported_extensions: string[]; allowed_domains: string[] }

export default function KnowledgePage() {
  const { can } = useAuth()
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const { data, error, reload } = useLoad(() => get<ListResp>(`/api/knowledge?${new URLSearchParams({ ...(status ? { status } : {}), ...(q ? { q } : {}) })}`), [status, q])
  const [modal, setModal] = useState<'' | 'upload' | 'url' | 'faq' | 'search'>('')
  const [editing, setEditing] = useState<string | null>(null)
  const { run } = useAction()
  const setSourceStatus = async (s: Source, st: string) => { await run(() => post(`/api/knowledge/${s.id}/status`, { status: st }), `Source ${st}`); reload() }
  const remove = async (s: Source) => {
    if (!confirm(`Delete "${s.title}" permanently?`)) return
    await run(() => del(`/api/knowledge/${s.id}`), 'Deleted'); reload()
  }
  return (
    <div className="space-y-4">
      <PageHeader title="Knowledge" sub={data ? `${data.items.length} sources · ${data.indexed_chunks} chunks indexed from approved sources` : ''}
        actions={<>
          <Button onClick={() => setModal('search')}><Search className="h-4 w-4" /> Test retrieval</Button>
          {can('manager') && <>
            <Button onClick={() => setModal('faq')}><MessageSquarePlus className="h-4 w-4" /> Add FAQ / text</Button>
            <Button onClick={() => setModal('url')}><Globe className="h-4 w-4" /> Add web page</Button>
            <Button variant="primary" onClick={() => setModal('upload')}><FileUp className="h-4 w-4" /> Upload document</Button>
          </>}
        </>} />
      <Notice tone="blue">Only <b>approved</b> sources are used to answer callers. New or edited sources start as drafts and must be reviewed and approved by a manager. The agent will say it doesn't know rather than answer from unapproved or missing information.</Notice>
      {data && data.sample_sources_live > 0 && <Notice>{data.sample_sources_live} approved source(s) are marked <b>sample</b> (public website summaries). Verify, edit and untick "sample" — or archive them — before production.</Notice>}
      <div className="flex flex-wrap gap-2">
        <Input placeholder="Search title or content…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
          <option value="">All statuses</option><option value="approved">Approved</option><option value="draft">Draft</option><option value="archived">Archived</option>
        </Select>
      </div>
      <ErrorBox error={error} />
      <Card pad={false}>
        <Table head={['Title', 'Type', 'Category', 'Status', 'Chunks', 'Updated', '']}>
          {data?.items.map((s) => (
            <tr key={s.id} className="hover:bg-stone-50">
              <Td>
                <button className="text-left font-medium text-brand-700 hover:underline" onClick={() => setEditing(s.id)}>{s.title}</button>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {s.is_sample && <Badge color="amber">sample</Badge>}
                  {s.ingest_status === 'error' && <Badge color="red">ingest error</Badge>}
                  {s.tags.map((t) => <Badge key={t}>{t}</Badge>)}
                </div>
                {s.error && <div className="mt-1 text-xs text-red-600">{s.error}</div>}
              </Td>
              <Td className="capitalize">{s.kind}{s.filename ? <div className="text-xs text-stone-500">{s.filename}</div> : null}</Td>
              <Td className="capitalize">{s.category}</Td>
              <Td><StatusBadge value={s.status} /></Td>
              <Td>{s.chunk_count}</Td>
              <Td className="whitespace-nowrap text-xs text-stone-500">{fmtDate(s.updated_at)}<div>{s.approved_by ? `approved by ${s.approved_by}` : `by ${s.created_by}`}</div></Td>
              <Td className="whitespace-nowrap">
                {can('manager') && <div className="flex gap-1">
                  {s.status !== 'approved' && <Button size="sm" onClick={() => setSourceStatus(s, 'approved')} title="Approve"><CheckCircle2 className="h-3.5 w-3.5" /> Approve</Button>}
                  {s.status === 'approved' && <Button size="sm" onClick={() => setSourceStatus(s, 'archived')} title="Archive"><Archive className="h-3.5 w-3.5" /></Button>}
                  <Button size="sm" variant="ghost" onClick={() => setEditing(s.id)} title="Edit"><Pencil className="h-3.5 w-3.5" /></Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(s)} title="Delete"><Trash2 className="h-3.5 w-3.5 text-red-600" /></Button>
                </div>}
              </Td>
            </tr>
          ))}
        </Table>
        {data?.items.length === 0 && <Empty>No knowledge sources. Upload documents or add FAQs to get started.</Empty>}
      </Card>
      {modal === 'upload' && <UploadModal exts={data?.supported_extensions ?? []} onClose={() => setModal('')} onDone={reload} />}
      {modal === 'url' && <UrlModal domains={data?.allowed_domains ?? []} onClose={() => setModal('')} onDone={reload} />}
      {modal === 'faq' && <FaqModal onClose={() => setModal('')} onDone={reload} />}
      {modal === 'search' && <SearchModal onClose={() => setModal('')} />}
      {editing && <EditModal id={editing} onClose={() => setEditing(null)} onDone={reload} canEdit={can('manager')} />}
    </div>
  )
}

function UploadModal({ exts, onClose, onDone }: { exts: string[]; onClose: () => void; onDone: () => void }) {
  const file = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('general')
  const [tags, setTags] = useState('')
  const { busy, run } = useAction()
  const submit = async () => {
    const f = file.current?.files?.[0]
    if (!f) return
    const fd = new FormData()
    fd.append('file', f); fd.append('title', title); fd.append('category', category); fd.append('tags', tags)
    const r = await run(() => upload<Source>('/api/knowledge/upload', fd), 'Uploaded — review and approve it to make it live')
    if (r) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title="Upload document">
      <div className="space-y-3">
        <Field label="File" hint={`Supported: ${exts.join(', ')} (max 25 MB). CSV files with question/answer columns become FAQs.`}>
          <input ref={file} type="file" accept={exts.join(',')} className="block w-full text-sm" />
        </Field>
        <Field label="Title (optional)"><Input value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Category"><Input value={category} onChange={(e) => setCategory(e.target.value)} /></Field>
          <Field label="Tags (comma separated)"><Input value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        </div>
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit}>Upload</Button></div>
      </div>
    </Modal>
  )
}

function UrlModal({ domains, onClose, onDone }: { domains: string[]; onClose: () => void; onDone: () => void }) {
  const [url, setUrl] = useState('https://bongohive.co.zm/')
  const [category, setCategory] = useState('website')
  const { busy, run } = useAction()
  const submit = async () => {
    if (await run(() => post('/api/knowledge/url', { url, category }), 'Page imported as draft')) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title="Import an approved web page">
      <div className="space-y-3">
        <Field label="URL" hint={`Allowed domains: ${domains.join(', ')} (configure KNOWLEDGE_ALLOWED_DOMAINS)`}><Input value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Category"><Input value={category} onChange={(e) => setCategory(e.target.value)} /></Field>
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit}>Import</Button></div>
      </div>
    </Modal>
  )
}

function FaqModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('faq')
  const [mode, setMode] = useState<'faq' | 'text'>('faq')
  const [faqs, setFaqs] = useState([{ question: '', answer: '' }])
  const [content, setContent] = useState('')
  const { busy, run } = useAction()
  const submit = async () => {
    const body = mode === 'faq' ? { title, category, faqs: faqs.filter((f) => f.question && f.answer) } : { title, category, content }
    if (await run(() => post('/api/knowledge/text', body), 'Saved as draft')) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title="Add FAQ or text" wide>
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Title"><Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Coworking FAQ (verified)" /></Field>
          <Field label="Category"><Input value={category} onChange={(e) => setCategory(e.target.value)} /></Field>
          <Field label="Format"><Select value={mode} onChange={(e) => setMode(e.target.value as 'faq' | 'text')}><option value="faq">Question & answer pairs</option><option value="text">Free text / markdown</option></Select></Field>
        </div>
        {mode === 'faq' ? (
          <div className="space-y-3">
            {faqs.map((f, i) => (
              <div key={i} className="grid gap-2 rounded-lg border border-stone-200 p-3 sm:grid-cols-2">
                <Input placeholder="Question callers ask" value={f.question} onChange={(e) => setFaqs(faqs.map((x, j) => j === i ? { ...x, question: e.target.value } : x))} />
                <Textarea rows={2} placeholder="Verified answer" value={f.answer} onChange={(e) => setFaqs(faqs.map((x, j) => j === i ? { ...x, answer: e.target.value } : x))} />
              </div>
            ))}
            <Button size="sm" onClick={() => setFaqs([...faqs, { question: '', answer: '' }])}><Plus className="h-3.5 w-3.5" /> Add question</Button>
          </div>
        ) : (
          <Textarea rows={14} value={content} onChange={(e) => setContent(e.target.value)} placeholder={'# Heading\n\nParagraphs of verified information. Use ## headings for each topic.'} />
        )}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit} disabled={!title}>Save draft</Button></div>
      </div>
    </Modal>
  )
}

function EditModal({ id, onClose, onDone, canEdit }: { id: string; onClose: () => void; onDone: () => void; canEdit: boolean }) {
  const { data, error } = useLoad(() => get<Source>(`/api/knowledge/${id}`), [id])
  const [draft, setDraft] = useState<Partial<Source> | null>(null)
  const { busy, run } = useAction()
  const cur = { ...data, ...draft } as Source
  const save = async () => {
    if (!draft) return
    const body: Record<string, unknown> = { ...draft }
    if (typeof body.tags === 'string') body.tags = (body.tags as string).split(',').map((t) => t.trim()).filter(Boolean)
    if (await run(() => patch(`/api/knowledge/${id}`, body), draft.content !== undefined ? 'Saved — content changes need re-approval' : 'Saved')) { onDone(); onClose() }
  }
  return (
    <Modal open onClose={onClose} title={data?.title ?? 'Source'} wide>
      <ErrorBox error={error} />
      {data && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Title"><Input disabled={!canEdit} value={cur.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
            <Field label="Category"><Input disabled={!canEdit} value={cur.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} /></Field>
            <Field label="Tags"><Input disabled={!canEdit} value={Array.isArray(cur.tags) ? cur.tags.join(', ') : String(cur.tags)} onChange={(e) => setDraft({ ...draft, tags: e.target.value as unknown as string[] })} /></Field>
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!canEdit} checked={cur.is_sample} onChange={(e) => setDraft({ ...draft, is_sample: e.target.checked })} /> Sample / unverified content</label>
          <Field label={`Content (${(cur.content ?? '').length.toLocaleString()} characters)`} hint="Use ## headings to separate topics; each section is retrieved independently.">
            <Textarea disabled={!canEdit} rows={18} className="font-mono text-xs" value={cur.content ?? ''} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
          </Field>
          {canEdit && <div className="flex justify-end gap-2"><Button onClick={onClose}>Close</Button><Button variant="primary" loading={busy} disabled={!draft} onClick={save}>Save</Button></div>}
        </div>
      )}
    </Modal>
  )
}

function SearchModal({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const { busy, run } = useAction()
  const [res, setRes] = useState<{ elapsed_ms: number; hits: { source_title: string; heading: string; text: string; dense: number; is_sample: boolean }[] } | null>(null)
  const search = async () => setRes((await run(() => post<typeof res>('/api/knowledge/search', { query, top_k: 5 }))) ?? null)
  return (
    <Modal open onClose={onClose} title="Test retrieval" wide>
      <div className="space-y-3">
        <div className="flex gap-2">
          <Input placeholder="Ask a question as a caller would…" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
          <Button variant="primary" loading={busy} onClick={search}>Search</Button>
        </div>
        {res && (
          <div className="space-y-2">
            <div className="text-xs text-stone-500">{res.hits.length} result(s) above the relevance threshold in {res.elapsed_ms} ms. No results means the agent will say it doesn't know.</div>
            {res.hits.map((h, i) => (
              <div key={i} className="rounded-lg border border-stone-200 p-3 text-sm">
                <div className="mb-1 flex flex-wrap items-center gap-2 font-medium">{h.source_title}{h.heading && <span className="text-stone-500">› {h.heading}</span>}<Badge color="blue">similarity {h.dense}</Badge>{h.is_sample && <Badge color="amber">sample</Badge>}</div>
                <div className="whitespace-pre-wrap text-stone-600">{h.text}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  )
}
