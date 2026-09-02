# A private CA for the forwarding leg

Put your SIEM's issuing certificate here and name it:

    AIRA_OTEL_FORWARD_CA_FILE=/etc/otelcol-contrib/ca/your-ca.crt

The directory is mounted read-only into the collector at `/etc/otelcol-contrib/ca`. Everything in
it except this file is git-ignored — a certificate is not a secret, but whose certificate it is
says something about the installation, and neither belongs in a public repository.

Without this, the only way to reach a SIEM behind a private CA was `AIRA_OTEL_FORWARD_INSECURE=true`,
which answers a missing root by not verifying any certificate at all.
