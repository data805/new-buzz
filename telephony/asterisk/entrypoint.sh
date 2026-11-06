#!/bin/sh
# Render Asterisk config from templates using environment variables, so SIP
# credentials live only in .env / the secret store, never in the repository.
set -eu

: "${SIP_TRUNK_HOST:?SIP_TRUNK_HOST is required (your provider's SIP server)}"
: "${ASTERISK_AMI_PASSWORD:?ASTERISK_AMI_PASSWORD is required}"
: "${ASTERISK_CALLBACK_TOKEN:?ASTERISK_CALLBACK_TOKEN is required}"
export SIP_TRUNK_PORT="${SIP_TRUNK_PORT:-5060}"
export SIP_TRUNK_USERNAME="${SIP_TRUNK_USERNAME:-}"
export SIP_TRUNK_PASSWORD="${SIP_TRUNK_PASSWORD:-}"
export SIP_TRUNK_TRANSPORT="${SIP_TRUNK_TRANSPORT:-udp}"
export SIP_EXTERNAL_IP="${SIP_EXTERNAL_IP:-}"
export SIP_LOCAL_NET="${SIP_LOCAL_NET:-10.0.0.0/8}"
export SIP_DID="${SIP_DID:-s}"
export ASTERISK_AMI_USER="${ASTERISK_AMI_USER:-voiceagent}"
export VOICEAGENT_HOST="${VOICEAGENT_HOST:-127.0.0.1}"
export VOICEAGENT_HTTP_PORT="${VOICEAGENT_HTTP_PORT:-8000}"
export AUDIOSOCKET_PORT="${AUDIOSOCKET_PORT:-9092}"
export RTP_START="${RTP_START:-10000}"
export RTP_END="${RTP_END:-10200}"

# Only substitute our own variables; Asterisk's ${EXTEN}, ${CALLERID(num)} etc. must survive.
VARS='$SIP_TRUNK_HOST $SIP_TRUNK_PORT $SIP_TRUNK_USERNAME $SIP_TRUNK_PASSWORD $SIP_TRUNK_TRANSPORT $SIP_EXTERNAL_IP $SIP_LOCAL_NET $SIP_DID $ASTERISK_AMI_USER $ASTERISK_AMI_PASSWORD $ASTERISK_CALLBACK_TOKEN $VOICEAGENT_HOST $VOICEAGENT_HTTP_PORT $AUDIOSOCKET_PORT $RTP_START $RTP_END'
for t in /etc/asterisk/templates/*.conf.template; do
  out="/etc/asterisk/$(basename "$t" .template)"
  envsubst "$VARS" < "$t" > "$out"
done

# Registration is optional: IP-authenticated trunks leave SIP_TRUNK_USERNAME empty.
if [ -z "$SIP_TRUNK_USERNAME" ]; then
  sed -i '/^;REGISTRATION-BEGIN/,/^;REGISTRATION-END/d' /etc/asterisk/pjsip.conf
  sed -i 's/^outbound_auth=zm-trunk-auth$//' /etc/asterisk/pjsip.conf
fi

exec asterisk -f -vvv
