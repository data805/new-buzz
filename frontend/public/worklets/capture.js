// Captures microphone audio, resamples to 16 kHz and posts Int16 frames of 512 samples.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / 16000
    this.pos = 0
    this.buf = new Int16Array(512)
    this.n = 0
    this.prev = 0
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    // linear-interpolation resampler with a crude averaging low-pass when downsampling
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos)
      const frac = this.pos - i
      const a = i === 0 ? this.prev : ch[i - 1]
      const b = ch[i]
      let s = a + (b - a) * frac
      if (this.ratio >= 2 && i > 0) s = (s + ch[i - 1]) / 2
      this.buf[this.n++] = Math.max(-32768, Math.min(32767, s * 32767))
      if (this.n === 512) {
        this.port.postMessage(this.buf.buffer.slice(0))
        this.n = 0
      }
      this.pos += this.ratio
    }
    this.pos -= ch.length
    this.prev = ch[ch.length - 1]
    let peak = 0
    for (let i = 0; i < ch.length; i++) peak = Math.max(peak, Math.abs(ch[i]))
    this.port.postMessage({ level: peak })
    return true
  }
}
registerProcessor('capture-processor', CaptureProcessor)
