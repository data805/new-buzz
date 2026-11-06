// Automated live call through the dashboard's Test console, in a visible Chrome window.
//
// Opens the console, starts a browser call, types each question into "Type instead of
// speaking", waits for the agent's spoken reply, then opens the call in Call history.
// You hear the agent through your speakers; no microphone is used.
//
// Needs the server running with AUTH_DISABLED=true (e.g. scripts/run_demo.sh), or pass
// --email/--password to sign in. Uses the installed Google Chrome.
//
//   node scripts/browser_call_demo.mjs                      # http://localhost:8080, visible window
//   node scripts/browser_call_demo.mjs --base http://localhost:8000 --email a@b.c --password '...'
//   node scripts/browser_call_demo.mjs --headless --question "Where are you located?"
import { createRequire } from 'node:module'
import { parseArgs } from 'node:util'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { chromium } = require('playwright-core')

const { values: opt } = parseArgs({
  options: {
    base: { type: 'string', default: 'http://localhost:8080' },
    email: { type: 'string' },
    password: { type: 'string' },
    headless: { type: 'boolean', default: false },
    question: { type: 'string', multiple: true },
    'turn-timeout': { type: 'string', default: '180' },
  },
})

const QUESTIONS = opt.question ?? [
  'Hi, where is BongoHive located?',
  'Does the AI Lab offer a free AI assessment for my business?',
  'How much does a hot desk cost per month?',
  'Who is the CEO of BongoHive?',
  'My name is Mwila Banda and my number is 0971234567. Please call me back tomorrow morning about the AI assessment.',
  "That's all, thank you. Goodbye.",
]
const TURN_TIMEOUT = Number(opt['turn-timeout']) * 1000
const t0 = Date.now()
const ts = () => `${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s`

const browser = await chromium.launch({
  channel: 'chrome',
  headless: opt.headless,
  args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'],
})
const page = await (await browser.newContext({ viewport: { width: 1360, height: 860 } })).newPage()
page.on('pageerror', (e) => console.log(ts(), 'PAGE ERROR', e.message))

// Follow the call over its websocket so we know exactly when each reply has finished.
const call = { state: 'idle', ended: null, callId: null, turnDone: null }
page.on('websocket', (ws) => {
  if (!ws.url().includes('/ws/voice')) return
  ws.on('framereceived', ({ payload }) => {
    if (typeof payload !== 'string') return
    const ev = JSON.parse(payload)
    if (ev.type === 'call_started') call.callId = ev.call_id
    if (ev.type === 'assistant') console.log(ts(), 'AGENT ', ev.text)
    if (ev.type === 'transcript') console.log(ts(), 'CALLER', ev.text)
    if (ev.type === 'state') {
      call.state = ev.state
      if (ev.state === 'listening') call.turnDone?.()
    }
    if (ev.type === 'call_ended') { call.ended = ev.outcome; call.turnDone?.() }
  })
})
const waitForListening = (what) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error(`timed out waiting for ${what}`)), TURN_TIMEOUT)
  call.turnDone = () => { clearTimeout(timer); call.turnDone = null; resolve() }
})

try {
  if (opt.email) {
    const r = await page.request.post(`${opt.base}/api/auth/login`, { data: { email: opt.email, password: opt.password } })
    if (!r.ok()) throw new Error(`login failed: ${r.status()} ${await r.text()}`)
  }
  await page.goto(`${opt.base}/test`)
  await page.getByRole('button', { name: 'Start call' }).waitFor({ timeout: 15000 }).catch(() => {
    throw new Error('Test console not reachable: is the server running with AUTH_DISABLED=true (or pass --email/--password)?')
  })
  await page.getByLabel('Use microphone').uncheck()

  const greeting = waitForListening('the greeting')
  await page.getByRole('button', { name: 'Start call' }).click()
  console.log(ts(), 'call started')
  await greeting

  const input = page.getByPlaceholder('Type instead of speaking…')
  for (const q of QUESTIONS) {
    if (call.ended) break
    const reply = waitForListening(`a reply to "${q}"`)
    await input.fill(q)
    await input.press('Enter')
    console.log(ts(), 'TYPED ', q)
    await reply
    await page.waitForTimeout(800) // let the last audio finish before the next question
  }

  if (!call.ended) {
    await page.getByRole('button', { name: 'Hang up' }).click()
    await page.waitForTimeout(2000)
  }
  console.log(ts(), `call ended: ${call.ended ?? 'hung up by script'}`)

  if (call.callId) {
    await page.waitForTimeout(3000) // summary + follow-ups are written after the call
    await page.goto(`${opt.base}/calls/${call.callId}`)
    console.log(ts(), `call record: ${opt.base}/calls/${call.callId}`)
    if (!opt.headless) await page.waitForTimeout(20000) // leave the record on screen
  }
} catch (e) {
  console.error(ts(), 'FAILED:', e.message)
  process.exitCode = 1
} finally {
  await browser.close()
}
