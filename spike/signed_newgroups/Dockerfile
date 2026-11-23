# The NNTP image with the PGP tool that INN's pgpverify needs to check signed control articles.
# Debian builds INN with gpg=/usr/bin/gpg1, and pgpverify prefers it over gpgv, so this is the
# package Debian suggests for inn2 (gnupg1). GnuPG 1.4 has no Ed25519: keys are RSA.
ARG BASE=ome-nntp:peering-test
FROM ${BASE}

RUN apt-get update \
	&& apt-get install --yes --no-install-recommends gnupg1 \
	&& rm -rf /var/lib/apt/lists/*

# pgpverify reads the public keys of the trusted signers from <pathetc>/pgp/pubring.gpg.
# The entrypoint makes /etc/news owned by root: only the admin can change the trusted keys.
RUN install -d -m 0755 /etc/news/pgp

# Only for the spike tests: keep INN's syslog messages (see syslog_to_file.py).
COPY syslog_to_file.py /usr/local/bin/syslog_to_file.py
