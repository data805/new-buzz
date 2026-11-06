// Plays queued PCM (Float32 at the context rate). 'clear' drops everything (barge-in).
class PlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.queue = []
    this.offset = 0
    this.port.onmessage = (e) => {
      if (e.data === 'clear') { this.queue = []; this.offset = 0; return }
      this.queue.push(e.data)
    }
  }
  process(_, outputs) {
    const out = outputs[0][0]
    let i = 0
    let peak = 0
    while (i < out.length && this.queue.length) {
      const cur = this.queue[0]
      const n = Math.min(out.length - i, cur.length - this.offset)
      for (let k = 0; k < n; k++) { const v = cur[this.offset + k]; out[i + k] = v; peak = Math.max(peak, Math.abs(v)) }
      i += n
      this.offset += n
      if (this.offset >= cur.length) { this.queue.shift(); this.offset = 0 }
    }
    for (; i < out.length; i++) out[i] = 0
    this.port.postMessage(peak)
    return true
  }
}
registerProcessor('player-processor', PlayerProcessor)
