#!/bin/sh
# Generate the self-signed certificates for the TLS peering test. Test use only.
#
#   tls-a.crt / tls-a.key        identity of the sidecar of node-a
#   tls-b.crt / tls-b.key        identity of the sidecar of node-b
#   intruder.crt / intruder.key  a certificate that no node trusts (negative test)
#
# Each sidecar trusts exactly the certificate of the other one (certificate pinning),
# so there is no CA. Usage: gen-certs.sh <output directory>
set -eu

out="${1:?usage: gen-certs.sh <output directory>}"
mkdir -p "${out}"

for name in tls-a tls-b intruder; do
	openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
		-keyout "${out}/${name}.key" -out "${out}/${name}.crt" \
		-days 30 -subj "/CN=${name}" -addext "subjectAltName=DNS:${name}" 2>/dev/null
	chmod 0644 "${out}/${name}.crt"
	chmod 0600 "${out}/${name}.key"
done
echo "Certificates written to ${out}"
