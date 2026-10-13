#!/bin/sh
# Provision additional databases/roles on first Postgres init (FRD-000).
#
# The Keycloak password reaches SQL as a psql variable, `:'kcpass'`, which psql quotes as a
# literal — never spliced into the statement by the shell. A password with a `'` in it otherwise
# ends the literal early, `ON_ERROR_STOP` aborts the first-time init, and the cluster comes up
# without the Keycloak database. The heredoc is quoted for the same reason: nothing in it is the
# shell's to expand.
set -e
psql -v ON_ERROR_STOP=1 -v kcpass="$KC_DB_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'SQL'
    CREATE DATABASE aira_gateway;
    CREATE DATABASE aira_mgmt;
    CREATE ROLE keycloak WITH LOGIN PASSWORD :'kcpass';
    CREATE DATABASE keycloak OWNER keycloak;
SQL
echo "[init] created databases: aira_gateway, aira_mgmt, keycloak"
