#!/bin/sh
# Provision additional databases/roles on first Postgres init (FRD-000).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE DATABASE aira_gateway;
    CREATE DATABASE aira_mgmt;
    CREATE ROLE keycloak WITH LOGIN PASSWORD '${KC_DB_PASSWORD}';
    CREATE DATABASE keycloak OWNER keycloak;
SQL
echo "[init] created databases: aira_gateway, aira_mgmt, keycloak"
