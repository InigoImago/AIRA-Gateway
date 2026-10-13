# A Windows domain for the development stack (`FRD-140`) — build from the repository root:
#   docker build -f deploy/compose/ad/Dockerfile -t aira-ad .
#
# Samba as an Active Directory domain controller: the directory Keycloak reads over LDAP and the
# KDC that issues the tickets a Windows sign-on presents. It stands in for an installation's own
# AD so that both halves of the sign-on can be proven in this stack; no deployment runs it.
#
# MIT Kerberos' client tools are here for the tests, which log a person on to the domain the way
# Windows does at the desktop (`kinit`) and hand the ticket to a browser.
FROM alpine:3.24

RUN apk add --no-cache samba-dc krb5

COPY deploy/compose/ad/provision.sh /usr/local/bin/provision.sh

ENTRYPOINT ["/usr/local/bin/provision.sh"]
