# Voice and language evaluation

## Text-to-speech candidates

| Engine | Licence | Naturalness (our listening) | CPU feasibility | Verdict |
|---|---|---|---|---|
| **Kokoro-82M v1.0** (ONNX) | Apache-2.0 (weights and code) | Best of the open-licence options: natural prosody, clear consonants, convincing pauses at commas; occasional flat intonation on long lists | ~82M params; real-time on a modern 4-core CPU; much faster on GPU | **Selected (default)** |
| Piper (VITS) | GPL-3.0 (piper1-gpl), voices mostly CC-BY/MIT | Clearly synthetic: "reading voice", flatter pitch | Very light (several × real time on a Raspberry Pi in normal conditions) | **Fallback** for weak hardware; optional install because of the GPL |
| XTTS-v2 (Coqui) | Coqui Public Model Licence (non-commercial) | Very natural, voice cloning | Needs GPU for real time | Rejected: licence |
| F5-TTS / E2-TTS | Code MIT, weights CC-BY-NC | Very natural, cloning | GPU | Rejected: weights licence |
| Orpheus-3B | Apache-2.0 | Very expressive | Needs a strong GPU for real time | Future option on a GPU server |
| Meta MMS-TTS (incl. Bemba `bem`, Nyanja `nya`) | CC-BY-NC-4.0 | Intelligible, robotic | Light | Not for production (non-commercial); useful for local-language experiments |

### Kokoro voices compared

Ten voices were synthesised with the same conversational greeting ("Hello, thank you for calling BongoHive. My name is Thandi, and I'm an AI assistant. How can I help you today?"). Samples are reproducible with `scripts/benchmark.py`, or preview them in **Agent settings → Voice**.

| Voice | Impression | Use |
|---|---|---|
| **af_heart** | Warm, friendly, natural rhythm; the most "human" of the set | **Default** |
| af_bella | Bright, energetic; slightly sales-like | Outbound reminders |
| af_sarah | Calm, measured | Support / complaints |
| af_nicole | Soft, slower (longest audio for the same text) | Not ideal on phone lines (too quiet) |
| bf_emma / bf_isabella | British accent, clear | Closer to Zambian English pronunciation of some words (e.g. "schedule", "programme") |
| am_michael / am_fenrir | Male US | If a male persona is preferred |
| bm_george / bm_fable | Male British; bm_fable is the fastest to synthesise | Male persona |

Kokoro's American and British phonemisers occasionally mispronounce local names, so the engine rewrites some before synthesis (`app/voice/tts.py: _TEXT_FIXES`): "BongoHive" → "Bongo Hive", "ZICTA", amounts like "K3" → "3 kwacha", and "+260". Add more entries as staff notice mispronunciations.

**Phone-line quality.** Telephony narrows audio to 8 kHz, which removes much of the difference between engines but also hides some artefacts. Always judge voices through a real phone call, not only the browser.

## Zambian English, accents and local languages

**Speech recognition (Whisper).** Whisper is trained on very diverse English and generally copes with Zambian-accented English. In our synthetic tests, `base.en` produced recognisable but imperfect transcripts of accented TTS speech ("who founded" heard as "who find it"; names and digit strings were garbled). `small.en` transcribed the same 4-second question perfectly. Recommendation: use **`small.en` on CPU** and **`large-v3-turbo` / `distil-large-v3` on GPU**. Domain hotwords (BongoHive, Lusaka, kwacha, network names) are passed to the decoder.

**Not yet measured:** word error rate on *real Zambian callers*. Synthetic voices are not a substitute. Collect 30–50 consented real calls, transcribe them by hand, and run `scripts/benchmark.py` with them added to `sample_data/audio/transcripts.txt`.

**Local languages.** There is no verified production-quality support today:

* Whisper does not officially support Bemba or Nyanja; zero-shot output is unusable. Community fine-tunes exist (e.g. a Whisper large-v3 Nyanja model reporting about 11% CER on FLEURS ([BuzzASR/nyanja](https://huggingface.co/BuzzASR/nyanja)), and Bemba fine-tunes from the IWSLT 2025 work ([arXiv 2505.02518](https://arxiv.org/pdf/2505.02518))), but we have **not tested** them and their licences need checking.
* The Zambezi Voice corpus (Bemba, Nyanja, Tonga, Lozi) is CC BY-NC-ND 4.0 ([arXiv 2306.04428](https://arxiv.org/html/2306.04428v2)), so it is not usable for commercial fine-tuning without permission.
* TTS: Meta MMS-TTS has Bemba and Nyanja checkpoints, but they are non-commercial and sound robotic.
* The LLM (Qwen2.5 3B/7B) has weak Bemba/Nyanja ability, so answers in those languages would be unreliable.

**We therefore do not claim multilingual capability.** The agent should politely continue in English and offer a callback from a staff member who speaks the caller's language.

### Improvement path

1. **Short term**: tune pronunciation fixes; pick a voice through phone-line A/B tests with staff; measure WER on real calls; switch STT to `small.en` (CPU) or `large-v3-turbo` (GPU).
2. **Zambian-accented English TTS**: record 2–4 hours of a consenting Zambian voice talent (scripted customer-service sentences), then fine-tune Kokoro/StyleTTS2 or Piper on it. Piper fine-tuning is well documented and cheap; Kokoro fine-tuning is possible via StyleTTS2 tooling.
3. **Local languages**: partner with UNZA / Zambezi Voice authors for a commercial licence, or collect BongoHive-owned data; fine-tune Whisper on Bemba/Nyanja; evaluate a multilingual LLM (or translate via NLLB-200, licence permitting); add a language-detection step that routes to the right models.
