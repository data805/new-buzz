# Asterisk 20 LTS with PJSIP, AudioSocket, AMD and CURL support (Alpine packages).
FROM alpine:3.22
RUN apk add --no-cache asterisk asterisk-sounds-en asterisk-curl asterisk-srtp gettext tini
COPY *.conf.template /etc/asterisk/templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
