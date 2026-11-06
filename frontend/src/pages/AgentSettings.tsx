import { useEffect, useState } from 'react'
import { Play, RotateCcw, Save } from 'lucide-react'
import { api, get, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Button, Card, ErrorBox, Field, Input, Notice, PageHeader, Select, Tabs, Textarea, toast, useAction, useLoad } from '../components/ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
type S = Record<string, any>
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function AgentSettingsPage() {
  const { can, user } = useAuth()
  const editable = can('manager')
  const { data, error } = useLoad(() => get<{ settings: S; defaults: S }>('/api/agent/settings'))
  const voices = useLoad(() => get<{ kokoro: { id: string; label: string }[]; piper: { id: string; label: string }[] }>('/api/agent/voices'))
  const [s, setS] = useState<S | null>(null)
  const [tab, setTab] = useState('persona')
  const { busy, run } = useAction()
  useEffect(() => { if (data) setS(structuredClone(data.settings)) }, [data])
  if (error) return <ErrorBox error={error} />
  if (!s || !data) return <div className="text-sm text-stone-500">Loading…</div>
  const set = (path: string, value: unknown) => {
    const next = structuredClone(s)
    const parts = path.split('.')
    let o = next
    for (const p of parts.slice(0, -1)) o = o[p]
    o[parts[parts.length - 1]] = value
    setS(next)
  }
  const dirty = JSON.stringify(s) !== JSON.stringify(data.settings)
  const save = async () => {
    const r = await run(() => put<{ settings: S; changed: string[] }>('/api/agent/settings', s))
    if (r) { data.settings = r.settings; setS(structuredClone(r.settings)); toast(r.changed.length ? `Saved (${r.changed.join(', ')}) — applies to new calls` : 'No changes') }
  }
  return (
    <div className="space-y-4">
      <PageHeader title="Agent settings" sub="How the agent speaks, behaves and escalates. Changes apply to new calls."
        actions={editable && <>
          <Button onClick={() => setS(structuredClone(data.settings))} disabled={!dirty}><RotateCcw className="h-4 w-4" /> Discard</Button>
          <Button variant="primary" onClick={save} loading={busy} disabled={!dirty}><Save className="h-4 w-4" /> Save changes</Button>
        </>} />
      {!editable && <Notice tone="blue">You have read-only access. Managers can edit these settings.</Notice>}
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'persona', label: 'Persona & prompts' }, { id: 'voice', label: 'Voice' }, { id: 'conversation', label: 'Conversation' },
        { id: 'hours', label: 'Hours & escalation' }, { id: 'privacy', label: 'Recording & privacy' }, { id: 'model', label: 'Model & knowledge' },
      ]} />
      <fieldset disabled={!editable} className="space-y-4">
        {tab === 'persona' && (
          <Card>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Agent name"><Input value={s.agent_name} onChange={(e) => set('agent_name', e.target.value)} /></Field>
              <Field label="Supported languages" hint="English is tested. See docs/VOICE_EVALUATION.md before enabling others.">
                <Input value={s.languages.join(', ')} onChange={(e) => set('languages', e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} />
              </Field>
              <Field label="Inbound greeting" hint="{agent_name} is replaced automatically. Must disclose that the caller is speaking to an AI.">
                <Textarea rows={3} value={s.greeting_inbound} onChange={(e) => set('greeting_inbound', e.target.value)} />
              </Field>
              <Field label="Outbound opening (default)" hint="Campaigns can override this.">
                <Textarea rows={3} value={s.greeting_outbound} onChange={(e) => set('greeting_outbound', e.target.value)} />
              </Field>
              <Field label="Goodbye"><Textarea rows={2} value={s.goodbye} onChange={(e) => set('goodbye', e.target.value)} /></Field>
              <Field label="Technical problem message"><Textarea rows={2} value={s.fallback_error} onChange={(e) => set('fallback_error', e.target.value)} /></Field>
            </div>
            <div className="mt-4">
              <Field label="System instructions (persona, style and accuracy rules)" hint="Keep the accuracy rules and control tags ([TRANSFER], [END_CALL]) — the call engine depends on them.">
                <Textarea rows={18} className="font-mono text-xs" value={s.persona} onChange={(e) => set('persona', e.target.value)} />
              </Field>
              <Button size="sm" variant="ghost" className="mt-2" onClick={() => set('persona', data.defaults.persona)}>Restore default instructions</Button>
            </div>
          </Card>
        )}
        {tab === 'voice' && <VoiceTab s={s} set={set} voices={voices.data} />}
        {tab === 'conversation' && (
          <Card>
            <div className="grid gap-4 md:grid-cols-3">
              {([
                ['turn_taking.endpoint_silence_ms', 'End-of-turn silence (ms)', 'How long the caller must pause before the agent replies. Lower = snappier, higher = fewer interruptions.'],
                ['turn_taking.min_speech_ms', 'Minimum speech (ms)', 'Ignore sounds shorter than this.'],
                ['turn_taking.vad_threshold', 'Speech detection threshold', '0.1–0.95; raise in noisy environments.'],
                ['turn_taking.barge_in_min_ms', 'Barge-in after (ms)', 'Caller must talk this long over the agent to interrupt it.'],
                ['turn_taking.silence_reprompt_s', 'Silence reprompt (s)', 'Ask "are you still there?" after this much silence.'],
                ['turn_taking.max_reprompts', 'Max reprompts', 'Then politely hang up.'],
                ['turn_taking.max_call_minutes', 'Max call length (min)', ''],
                ['turn_taking.max_utterance_s', 'Max caller utterance (s)', ''],
              ] as const).map(([path, label, hint]) => (
                <Field key={path} label={label} hint={hint}>
                  <Input type="number" step="any" value={path.split('.').reduce((o: any, k) => o[k], s)} onChange={(e) => set(path, Number(e.target.value))} />
                </Field>
              ))}
              <Field label="Barge-in (caller can interrupt)">
                <Select value={String(s.turn_taking.barge_in_enabled)} onChange={(e) => set('turn_taking.barge_in_enabled', e.target.value === 'true')}><option value="true">Enabled</option><option value="false">Disabled</option></Select>
              </Field>
              <Field label="Reprompt text"><Input value={s.reprompt} onChange={(e) => set('reprompt', e.target.value)} /></Field>
              <Field label="No-response goodbye"><Input value={s.no_response_goodbye} onChange={(e) => set('no_response_goodbye', e.target.value)} /></Field>
            </div>
          </Card>
        )}
        {tab === 'hours' && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Operating hours">
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {DAYS.map((d, i) => (
                    <label key={d} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={s.hours.days.includes(i)} onChange={(e) => set('hours.days', e.target.checked ? [...s.hours.days, i].sort() : s.hours.days.filter((x: number) => x !== i))} />{d}</label>
                  ))}
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <Field label="Opens"><Input type="time" value={s.hours.open} onChange={(e) => set('hours.open', e.target.value)} /></Field>
                  <Field label="Closes"><Input type="time" value={s.hours.close} onChange={(e) => set('hours.close', e.target.value)} /></Field>
                  <Field label="Time zone"><Input value={s.hours.timezone} onChange={(e) => set('hours.timezone', e.target.value)} /></Field>
                </div>
                <Field label="After-hours guidance"><Textarea rows={3} value={s.hours.after_hours_message} onChange={(e) => set('hours.after_hours_message', e.target.value)} /></Field>
              </div>
            </Card>
            <Card title="Escalation to humans">
              <div className="space-y-3">
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.escalation.transfer_enabled} onChange={(e) => set('escalation.transfer_enabled', e.target.checked)} /> Transfer calls to a human (target set by HUMAN_TRANSFER_TARGET)</label>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.escalation.transfer_only_in_hours} onChange={(e) => set('escalation.transfer_only_in_hours', e.target.checked)} /> Only transfer during operating hours</label>
                <Field label="Trigger phrases (one per line)"><Textarea rows={5} value={s.escalation.keywords.join('\n')} onChange={(e) => set('escalation.keywords', e.target.value.split('\n').map((x) => x.trim()).filter(Boolean))} /></Field>
                <Field label="Offer a human after this many unanswered questions"><Input type="number" value={s.escalation.max_unanswered} onChange={(e) => set('escalation.max_unanswered', Number(e.target.value))} /></Field>
                <Field label="Transfer message"><Input value={s.escalation.transfer_message} onChange={(e) => set('escalation.transfer_message', e.target.value)} /></Field>
                <Field label="No human available message"><Textarea rows={2} value={s.escalation.unavailable_message} onChange={(e) => set('escalation.unavailable_message', e.target.value)} /></Field>
              </div>
            </Card>
          </div>
        )}
        {tab === 'privacy' && (
          <Card>
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={s.ai_disclosure_required} disabled={user?.role !== 'admin'} onChange={(e) => set('ai_disclosure_required', e.target.checked)} />
                Always disclose that the caller is speaking with an AI (admin only to change; strongly recommended)
              </label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.recording_enabled} onChange={(e) => set('recording_enabled', e.target.checked)} /> Record calls (two-channel WAV). The disclosure below is spoken in every greeting when enabled.</label>
              <Field label="Recording disclosure"><Input value={s.recording_disclosure} onChange={(e) => set('recording_disclosure', e.target.value)} /></Field>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Keep transcripts (days)"><Input type="number" value={s.retention.transcripts_days} onChange={(e) => set('retention.transcripts_days', Number(e.target.value))} /></Field>
                <Field label="Keep recordings (days)"><Input type="number" value={s.retention.recordings_days} onChange={(e) => set('retention.recordings_days', Number(e.target.value))} /></Field>
                <label className="mt-6 flex items-center gap-2 text-sm"><input type="checkbox" checked={s.retention.redact_contact_details_in_transcripts} onChange={(e) => set('retention.redact_contact_details_in_transcripts', e.target.checked)} /> Redact phone numbers / emails in stored transcripts</label>
              </div>
              <Notice tone="blue">Retention runs every 6 hours. Data-subject erasure by phone number is under Security &amp; access → Privacy.</Notice>
            </div>
          </Card>
        )}
        {tab === 'model' && (
          <Card>
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="Temperature" hint="Lower is more factual and consistent."><Input type="number" step="0.05" value={s.llm_temperature} onChange={(e) => set('llm_temperature', Number(e.target.value))} /></Field>
              <Field label="Max reply tokens"><Input type="number" value={s.llm_max_tokens} onChange={(e) => set('llm_max_tokens', Number(e.target.value))} /></Field>
              <Field label="Conversation memory (turns)"><Input type="number" value={s.history_turns} onChange={(e) => set('history_turns', Number(e.target.value))} /></Field>
              <Field label="Knowledge passages per answer"><Input type="number" value={s.knowledge_top_k} onChange={(e) => set('knowledge_top_k', Number(e.target.value))} /></Field>
              <Field label="Relevance threshold" hint="Calibrated at 0.58 for bge-small; passages below are ignored so the agent admits it doesn't know."><Input type="number" step="0.01" value={s.knowledge_min_score} onChange={(e) => set('knowledge_min_score', Number(e.target.value))} /></Field>
            </div>
            <p className="mt-3 text-xs text-stone-500">The model itself (Ollama / OpenAI-compatible endpoint) is configured on the server with LLM_* environment variables; see System → Status.</p>
          </Card>
        )}
      </fieldset>
    </div>
  )
}

function VoiceTab({ s, set, voices }: { s: S; set: (p: string, v: unknown) => void; voices: { kokoro: { id: string; label: string }[]; piper: { id: string; label: string }[] } | null }) {
  const [text, setText] = useState("Hello, thank you for calling BongoHive. I'm Buzz, an AI assistant. Our coworking space is at ZEP-RE Business Park on Alick Nkhata Road. How can I help you today?")
  const [audio, setAudio] = useState<string | null>(null)
  const [info, setInfo] = useState('')
  const { busy, run } = useAction()
  const preview = async () => {
    const t0 = performance.now()
    const blob = await run(() => api<Blob>('/api/agent/tts-preview', { method: 'POST', json: { text, engine: s.voice.engine, voice: s.voice.voice, speed: s.voice.speed } }))
    if (blob) { setAudio(URL.createObjectURL(blob)); setInfo(`Generated in ${((performance.now() - t0) / 1000).toFixed(1)}s`) }
  }
  const list = (s.voice.engine === 'kokoro' ? voices?.kokoro : voices?.piper) ?? []
  return (
    <Card>
      <div className="grid gap-4 md:grid-cols-4">
        <Field label="Engine" hint="Kokoro: most natural. Piper: much faster on weak CPUs.">
          <Select value={s.voice.engine} onChange={(e) => { set('voice.engine', e.target.value) }}>
            <option value="kokoro">Kokoro-82M (Apache-2.0)</option><option value="piper">Piper (GPL-3.0, optional)</option>
          </Select>
        </Field>
        <Field label="Voice">
          <Select value={s.voice.voice} onChange={(e) => set('voice.voice', e.target.value)}>
            {!list.find((v) => v.id === s.voice.voice) && <option value={s.voice.voice}>{s.voice.voice}</option>}
            {list.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </Select>
        </Field>
        <Field label={`Speed (${s.voice.speed.toFixed(2)}×)`}><input type="range" min={0.7} max={1.3} step={0.05} value={s.voice.speed} onChange={(e) => set('voice.speed', Number(e.target.value))} className="w-full" /></Field>
        <Field label="Accent / phonemes"><Select value={s.voice.lang} onChange={(e) => set('voice.lang', e.target.value)}><option value="en-us">English (US)</option><option value="en-gb">English (UK)</option></Select></Field>
      </div>
      <div className="mt-4 space-y-2">
        <Field label="Preview text"><Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} /></Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={preview} loading={busy}><Play className="h-4 w-4" /> Preview voice</Button>
          {audio && <audio src={audio} controls autoPlay />}
          <span className="text-xs text-stone-500">{info}</span>
        </div>
        <p className="text-xs text-stone-500">No open TTS voice has a Zambian accent yet. See docs/VOICE_EVALUATION.md for the comparison and the improvement path (fine-tuning on local speech).</p>
      </div>
    </Card>
  )
}
