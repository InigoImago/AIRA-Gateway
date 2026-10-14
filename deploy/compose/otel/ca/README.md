# Certificates for the collector's two channels

Put a private CA's certificate here — and, for mutual TLS, a client certificate and its key — and
name them in the variables of the channel that needs them:

    AIRA_OTEL_FORWARD_CA_FILE=/etc/otelcol-contrib/ca/your-ca.crt
    AIRA_OTEL_FORWARD_CLIENT_CERT_FILE=/etc/otelcol-contrib/ca/client.crt
    AIRA_OTEL_FORWARD_CLIENT_KEY_FILE=/etc/otelcol-contrib/ca/client.key

`AIRA_OTEL_FORWARD_*` is the delivery channel (to a SIEM), `AIRA_OTEL_BACKEND_*` the observability
channel; each has the same three variables.

The directory is mounted read-only into the collector at `/etc/otelcol-contrib/ca`. Everything in
it except this file is git-ignored. A certificate is not a secret, but whose certificate it is says
something about the installation; a **client key is a secret**, and neither belongs in a
repository.

`AIRA_OTEL_<channel>_INSECURE=true` is not the answer to a private CA: it does not add a root, it
turns off certificate verification altogether.
