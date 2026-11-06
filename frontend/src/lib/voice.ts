// Browser side of a voice call over /ws/voice or /ws/simphone.
// Mic -> 16 kHz Int16 frames -> websocket; websocket PCM (24 kHz) -> player worklet.

export type VoiceEvent = { type: string; [k: string]: unknown }

export class VoiceClient {
  ws: WebSocket
  private capCtx?: AudioContext
  private playCtx?: AudioContext
  private player?: AudioWorkletNode
  private capture?: AudioWorkletNode
  private stream?: MediaStream
  private outRate = 24000
  muted = false
  onEvent: (e: VoiceEvent) => void = () => {}
  onLevel: (mic: number, speaker: number) => void = () => {}
  private micLevel = 0

  constructor(url: string) {
    this.ws = new WebSocket(url)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onmessage = (m) => this.handle(m)
    this.ws.onclose = (e) => this.onEvent({ type: 'socket_closed', code: e.code })
  }

  async startAudio(withMic = true) {
    this.playCtx = new AudioContext({ sampleRate: this.outRate })
    await this.playCtx.audioWorklet.addModule('/worklets/player.js')
    this.player = new AudioWorkletNode(this.playCtx, 'player-processor')
    this.player.port.onmessage = (e) => this.onLevel(this.micLevel, e.data as number)
    this.player.connect(this.playCtx.destination)
    if (!withMic) return
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    })
    this.capCtx = new AudioContext()
    await this.capCtx.audioWorklet.addModule('/worklets/capture.js')
    const src = this.capCtx.createMediaStreamSource(this.stream)
    this.capture = new AudioWorkletNode(this.capCtx, 'capture-processor')
    this.capture.port.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        if (!this.muted && this.ws.readyState === WebSocket.OPEN) this.ws.send(e.data)
      } else if (e.data && typeof e.data.level === 'number') {
        this.micLevel = this.muted ? 0 : e.data.level
      }
    }
    src.connect(this.capture)
  }

  private handle(m: MessageEvent) {
    if (m.data instanceof ArrayBuffer) {
      const i16 = new Int16Array(m.data)
      const f32 = new Float32Array(i16.length)
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768
      this.player?.port.postMessage(f32)
      return
    }
    const ev = JSON.parse(m.data) as VoiceEvent
    if (ev.type === 'ready' && typeof ev.out_rate === 'number') this.outRate = ev.out_rate
    if (ev.type === 'clear') this.player?.port.postMessage('clear')
    this.onEvent(ev)
  }

  send(obj: unknown) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj))
  }

  sendText(text: string) {
    this.send({ type: 'text', text })
  }

  hangup() {
    this.send({ type: 'hangup' })
  }

  close() {
    this.stream?.getTracks().forEach((t) => t.stop())
    this.capCtx?.close()
    this.playCtx?.close()
    if (this.ws.readyState <= WebSocket.OPEN) this.ws.close()
  }
}
