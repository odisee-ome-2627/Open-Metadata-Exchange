# Sidecar for the TLS peering test: stunnel, plus the tools the test needs
# (openssl for certificates and handshakes, netcat for a plain connection, tcpdump for the sniffer).
FROM debian:stable

RUN apt-get update \
    && apt-get install --yes --no-install-recommends stunnel4 openssl netcat-openbsd tcpdump \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["stunnel4"]
CMD ["/etc/stunnel/stunnel.conf"]
