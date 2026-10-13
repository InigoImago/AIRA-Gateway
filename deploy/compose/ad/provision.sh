#!/bin/sh
# Provision the development domain on the container's first start, then run its controller
# (`FRD-140`).
#
# What a Windows installation already has, and nothing it adds for AIRA: a domain, an
# organisational unit for people, the groups that carry AIRA's roles and its departments, and the
# account Keycloak binds with to read them. Keycloak's service principal and its keytab are the
# step a Windows sign-on adds, so they are not made here — `tools/windows_sso.py apply` makes them,
# the way the administrator of a real domain would.
#
# A recreated container is a new domain: new keys, a new bind password, a keytab that has to be
# exported again. Nothing outside the container remembers the old one, which is why `make
# windows-sso-up` applies the realm's half every time.
set -eu

: "${AD_REALM:?the Kerberos realm, e.g. AIRA.TEST}"
: "${AD_NETBIOS:?the NetBIOS name of the domain, e.g. AIRA}"
: "${AD_ROLE_GROUPS:?the groups that carry AIRA roles, separated by spaces}"
: "${AD_DEPARTMENTS:?the departments, separated by spaces}"
: "${AD_UPN_SUFFIX:?the suffix of people's user principal names, e.g. firma.example}"

PRIVATE=/var/lib/samba/private
# Written last: a provision that stopped half-way is not a domain, and is made again from nothing.
PROVISIONED="$PRIVATE/provisioned"
DNS_DOMAIN=$(printf '%s' "$AD_REALM" | tr '[:upper:]' '[:lower:]')
BASE_DN=$(printf 'DC=%s' "$DNS_DOMAIN" | sed 's/\./,DC=/g')

# A password nobody types: long, random, and of every class the domain's complexity rule counts.
secret() {
    printf 'Aa1-%s' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')"
}

provision() {
    rm -rf /var/lib/samba/* /etc/samba/smb.conf
    mkdir -p "$PRIVATE"
    # ACLs are kept in a TDB rather than in extended attributes: an unprivileged container may not
    # write the `security.*` namespace, and a stand-in that needs privileges is one CI cannot start.
    samba-tool domain provision --realm="$AD_REALM" --domain="$AD_NETBIOS" \
        --server-role=dc --dns-backend=NONE --adminpass="$(secret)" \
        --option="vfs objects = dfs_samba4 acl_xattr xattr_tdb" \
        --option="xattr_tdb:file = /var/lib/samba/xattr.tdb" >/dev/null
    # Keycloak binds with a password over plain LDAP inside the stack's network. A real domain
    # requires signing or TLS for that, and an installation's federation already meets it: what the
    # stand-in proves is the sign-on, not the transport.
    sed -i '/^\[global\]/a ldap server require strong auth = no' /etc/samba/smb.conf
    # The stand-in serves no DNS, so there is nothing to register the controller's names in.
    sed -i '/^\[global\]/a dns update command = /bin/true' /etc/samba/smb.conf

    # People's principal names end in the mail domain, as in most installations — an alternative
    # suffix the domain has to know before an account may carry it. Written where *Active Directory
    # Domains and Trusts* writes it, because the RPC that `samba-tool` would use needs a running
    # controller, and this one starts only once provisioning is done.
    python3 - "CN=Partitions,CN=Configuration,$BASE_DN" "$AD_UPN_SUFFIX" <<'PY'
import sys
import ldb
from samba.auth import system_session
from samba.param import LoadParm
from samba.samdb import SamDB

lp = LoadParm()
lp.load_default()
db = SamDB(url="/var/lib/samba/private/sam.ldb", session_info=system_session(), lp=lp)
message = ldb.Message(ldb.Dn(db, sys.argv[1]))
message["uPNSuffixes"] = ldb.MessageElement(sys.argv[2], ldb.FLAG_MOD_ADD, "uPNSuffixes")
db.modify(message)
PY

    samba-tool ou create "OU=AIRA,$BASE_DN" >/dev/null
    for ou in People Roles Departments "Service accounts"; do
        samba-tool ou create "OU=$ou,OU=AIRA,$BASE_DN" >/dev/null
    done
    for group in $AD_ROLE_GROUPS; do
        samba-tool group add "$group" --groupou="OU=Roles,OU=AIRA" >/dev/null
    done
    for group in $AD_DEPARTMENTS; do
        samba-tool group add "$group" --groupou="OU=Departments,OU=AIRA" >/dev/null
    done

    password=$(secret)
    samba-tool user create svc-keycloak-ldap "$password" \
        --userou="OU=Service accounts,OU=AIRA" >/dev/null
    samba-tool user setexpiry svc-keycloak-ldap --noexpiry >/dev/null
    (umask 077 && printf '%s' "$password" > "$PRIVATE/keycloak-bind.secret")
    touch "$PROVISIONED"
}

[ -f "$PROVISIONED" ] || provision

# The tests log people on inside this container, so its own KDC is the one its tools ask.
cat > /etc/krb5.conf <<EOF
[libdefaults]
    default_realm = $AD_REALM
    dns_lookup_kdc = false
    dns_lookup_realm = false
[realms]
    $AD_REALM = {
        kdc = 127.0.0.1
    }
EOF

exec samba -i --debug-stdout -d 1
