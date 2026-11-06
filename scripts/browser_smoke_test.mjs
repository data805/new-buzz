// Full functional smoke test of a running server through the real dashboard (Chrome).
//
//  1. Loads every dashboard page and fails on JavaScript errors or 5xx API responses.
//  2. Inbound: Test console browser call with a typed question; expects a grounded answer.
//  3. Outbound: a simulated handset goes online, the agent calls it (Outbound → single call),
//     the handset answers and speaks the --wav clip through a fake microphone.
//
// Needs AUTH_DISABLED=true (e.g. scripts/run_demo.sh) or --email/--password, and
// OUTBOUND_ENABLED=true with TELEPHONY_PROVIDER=simulator for step 3. Creates one test
// contact with a random +26097 number (consent source "smoke test").
//
//   node scripts/browser_smoke_test.mjs            # add --headed to watch it
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { parseArgs } from 'node:util'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { chromium } = require('playwright-core')

const { values: opt } = parseArgs({
  options: {
    base: { type: 'string', default: 'http://localhost:8080' },
    email: { type: 'string' },
    password: { type: 'string' },
    wav: { type: 'string', default: 'sample_data/audio/answer_after_pause.wav' },
    headed: { type: 'boolean', default: false },
  },
})
const PAGES = ['/', '/live', '/calls', '/records', '/knowledge', '/agent', '/outbound', '/workflows', '/test', '/system', '/admin']
const t0 = Date.now()
const ts = () => `${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s`
const results = []
const check = (name, ok, detail = '') => { results.push({ name, ok }); console.log(ts(), ok ? 'PASS' : 'FAIL', name, detail) }

// Chrome's fake microphone loops the file, so it should contain pauses (see below).
const browser = await chromium.launch({
  channel: 'chrome',
  headless: !opt.headed,
  args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${resolve(opt.wav)}`],
})
const ctx = await browser.newContext({ permissions: ['microphone'], viewport: { width: 1360, height: 860 } })
const api = ctx.request

function voiceEvents(page, path) {
  const events = []
  page.on('websocket', (ws) => {
    if (!ws.url().includes(path)) return
    ws.on('framereceived', ({ payload }) => { if (typeof payload === 'string') events.push(JSON.parse(payload)) })
  })
  return events
}
async function until(fn, ms, what) {
  const end = Date.now() + ms
  while (Date.now() < end) { const v = fn(); if (v) return v; await new Promise((r) => setTimeout(r, 250)) }
  throw new Error(`timed out waiting for ${what}`)
}

try {
  if (opt.email) {
    const r = await api.post(`${opt.base}/api/auth/login`, { data: { email: opt.email, password: opt.password } })
    if (!r.ok()) throw new Error(`login failed: ${r.status()}`)
  }

  // 1. every page renders without errors
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`) })
  for (const p of PAGES) {
    errors.length = 0
    await page.goto(opt.base + p)
    await page.waitForLoadState('networkidle')
    const signIn = await page.getByRole('button', { name: /sign in/i }).count()
    check(`page ${p}`, errors.length === 0 && !signIn, signIn ? '(login page shown)' : errors.join('; '))
  }

  // 2. inbound browser call, typed
  const inbound = voiceEvents(page, '/ws/voice')
  await page.goto(opt.base + '/test')
  await page.getByLabel('Use microphone').uncheck()
  await page.getByRole('button', { name: 'Start call' }).click()
  await until(() => inbound.some((e) => e.type === 'state' && e.state === 'listening'), 120000, 'inbound greeting')
  const input = page.getByPlaceholder('Type instead of speaking…')
  await input.fill('Where is BongoHive located?')
  await input.press('Enter')
  const answer = await until(() => {
    const said = inbound.filter((e) => e.type === 'assistant').map((e) => e.text).join(' ')
    return /ZEP-RE|Alick Nkhata/i.test(said) && said
  }, 180000, 'location answer')
  check('inbound call answers from knowledge', true, answer.slice(-110))
  await page.getByRole('button', { name: 'Hang up' }).click()
  await until(() => inbound.some((e) => e.type === 'call_ended'), 30000, 'inbound call end')
  check('inbound call ends cleanly', true)

  // 3. outbound call to a simulated handset
  const number = `+26097${String(Math.floor(Math.random() * 1e7)).padStart(7, '0')}`
  const c = await api.post(`${opt.base}/api/contacts`, { data: { phone: number, name: 'Smoke Test', consent_status: 'granted', consent_source: 'smoke test' } })
  check('consented contact created', c.ok(), c.ok() ? number : await c.text())
  const phone = await ctx.newPage()
  phone.on('pageerror', (e) => check('simulated phone page has no JS errors', false, e.message))
  const handset = voiceEvents(phone, '/ws/simphone')
  await phone.goto(opt.base + '/test')
  await phone.getByRole('button', { name: /Simulated phone/ }).click()
  await phone.getByLabel("This phone's number").fill(number)
  await phone.getByRole('button', { name: 'Go online' }).click()
  await until(() => handset.some((e) => e.type === 'registered'), 15000, 'handset registration')
  check('simulated handset online', true)

  const r = await api.post(`${opt.base}/api/calls/outbound`, { data: { phone: number, purpose: 'Confirm the caller is still interested in a free AI assessment from the BongoHive AI Lab.' } })
  const body = await r.json()
  check('outbound call placed', r.ok(), r.ok() ? body.call_id : JSON.stringify(body))
  if (!r.ok()) throw new Error('outbound call refused')
  await phone.getByRole('button', { name: 'Answer' }).click({ timeout: 60000 })
  check('handset rang and was answered', true)
  const first = await until(() => handset.find((e) => e.type === 'assistant'), 120000, 'agent greeting')
  check('agent first says it is an AI (disclosure)', /\bA\.?I\.?\b/.test(first.text), first.text.slice(0, 110))
  const heard = await until(() => handset.find((e) => e.type === 'transcript'), 120000, 'caller speech recognised')
  check('agent hears the called party', true, `"${heard.text}"`)
  const reply = await until(() => {
    const i = handset.indexOf(heard)
    // the reply proper, not the AI-disclosure line that may precede it
    return handset.slice(i).find((e) => e.type === 'assistant' && !/an AI assistant/.test(e.text))
  }, 180000, 'agent reply to called party')
  check('agent replies to the called party', true, reply.text.slice(0, 110))
  await phone.getByRole('button', { name: 'Hang up' }).click()
  await until(() => handset.some((e) => e.type === 'call_ended'), 30000, 'outbound call end')

  let rec
  for (let i = 0; i < 40; i++) {
    try {
      rec = await (await api.get(`${opt.base}/api/calls/${body.call_id}`)).json()
    } catch { rec = {} } // idle keep-alive connection reset; just poll again
    if (rec.summary) break
    await new Promise((r2) => setTimeout(r2, 3000))
  }
  check('outbound call recorded with summary', rec.direction === 'outbound' && !!rec.summary, `${rec.status}/${rec.outcome}: ${(rec.summary || '').slice(0, 100)}`)
} catch (e) {
  check('smoke test run', false, e.message)
} finally {
  await browser.close()
}
const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exitCode = failed.length ? 1 : 0
