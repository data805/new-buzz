#!/usr/bin/env bash
# Telephony integration test without a SIP trunk.
#
# Starts the Asterisk image, then places a call from a fake caller (a Local
# channel that plays a recorded question) into the real `from-trunk` dialplan:
# CURL() registration -> AudioSocket -> the agent -> audio back through Asterisk.
# The caller side is recorded with MixMonitor and transcribed with Whisper.
#
# Prerequisites: Docker; the app running with the Asterisk provider, e.g.
#   cd backend && TELEPHONY_PROVIDER=asterisk ASTERISK_AMI_HOST=127.0.0.1 \
#     ASTERISK_AMI_PASSWORD=test-ami-secret ASTERISK_CALLBACK_TOKEN=test-callback-token \
#     uvicorn app.main:app --host 0.0.0.0 --port 8001
#
# Usage: scripts/asterisk_integration_test.sh [host-address-seen-from-containers]
#   (Docker Desktop: host.docker.internal, colima: 192.168.5.2, Linux: 172.17.0.1)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST_ADDR="${1:-host.docker.internal}"
PY=".venv/bin/python"
OUT="$(mktemp -d)"

docker build -q -t voiceagent-asterisk:test telephony/asterisk >/dev/null
docker rm -f va-asterisk-test >/dev/null 2>&1 || true
docker run -d --name va-asterisk-test -p 127.0.0.1:5038:5038 \
  -e SIP_TRUNK_HOST=127.0.0.1 -e ASTERISK_AMI_PASSWORD=test-ami-secret \
  -e ASTERISK_CALLBACK_TOKEN=test-callback-token -e VOICEAGENT_HOST="$HOST_ADDR" \
  -e VOICEAGENT_HTTP_PORT=8001 -e AUDIOSOCKET_PORT=9092 voiceagent-asterisk:test >/dev/null
until docker logs va-asterisk-test 2>&1 | grep -q "Asterisk Ready"; do sleep 1; done
echo "Asterisk ready"

"$PY" - "$OUT/q.wav" <<'EOF'
import sys, soundfile as sf
sys.path.insert(0, "backend")
from app.voice.audio import resample
a, sr = sf.read("sample_data/audio/q_location.wav", dtype="float32")
sf.write(sys.argv[1], resample(a, sr, 8000), 8000, subtype="PCM_16")
EOF
docker cp "$OUT/q.wav" va-asterisk-test:/usr/share/asterisk/sounds/en/q_location.wav
docker exec va-asterisk-test sh -c 'cat >> /etc/asterisk/extensions.conf <<EOT

[test-caller]
exten => s,1,Answer()
 same => n,MixMonitor(/tmp/test-call.wav)
 same => n,Wait(12)
 same => n,Playback(q_location)
 same => n,Wait(120)
 same => n,Hangup()
EOT
asterisk -rx "dialplan reload" >/dev/null
asterisk -rx "channel originate Local/s@test-caller extension 0211845600@from-trunk"'
echo "Call placed; waiting for it to finish (up to 3 minutes)..."
for _ in $(seq 1 180); do
  docker exec va-asterisk-test asterisk -rx "core show channels count" | grep -q "^0 active channels" && break
  sleep 1
done
docker cp va-asterisk-test:/tmp/test-call.wav "$OUT/call.wav"
"$PY" - "$OUT/call.wav" <<'EOF'
import sys, soundfile as sf
sys.path.insert(0, "backend")
from faster_whisper import WhisperModel
from app.voice.audio import resample
a, sr = sf.read(sys.argv[1], dtype="float32")
print(f"Recorded {len(a)/sr:.1f}s at {sr} Hz. Transcript of the call (caller + agent):")
for s in WhisperModel("base.en", device="cpu", compute_type="int8", download_root="models/whisper").transcribe(resample(a, sr, 16000), language="en")[0]:
    print(f"  [{s.start:5.1f}s] {s.text.strip()}")
EOF
docker rm -f va-asterisk-test >/dev/null
echo "Recording kept at $OUT/call.wav"
