#!/bin/bash
# Phase 17B-1: local-dev bootstrap for the restricted runtime application
# role. Mounted into the `postgres` service's
# /docker-entrypoint-initdb.d/ (see docker-compose.yml) — the official
# postgres/pgvector image runs every *.sh/*.sql file there exactly once,
# only when the data directory is being initialized for the FIRST time
# (i.e. a brand-new `postgres_data` volume). It does NOT re-run against
# an existing volume — see docker-compose.yml's comment on the postgres
# service for how to provision the role into an already-existing local
# dev database.
#
# This script creates a second role, `$APP_DB_USER`, distinct from the
# cluster's own bootstrap/superuser role (`$POSTGRES_USER`, still used
# for migrations/administration — see DATABASE_MIGRATION_URL). The new
# role is LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS,
# NOREPLICATION, and never granted ownership of any table — it only ever
# receives explicit GRANTs, which is the whole point: PostgreSQL
# superusers and table owners both unconditionally bypass Row-Level
# Security, which is exactly why the application's normal runtime
# connection must stop being either of those (see
# PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md §7).
#
# ALTER DEFAULT PRIVILEGES here is what makes this safe to run BEFORE any
# Alembic migration has ever applied (which is always true the first time
# a fresh volume is initialized — this script runs before the backend
# container's own migrations do): it does not grant anything on tables
# that don't exist yet, it tells Postgres "whenever role $POSTGRES_USER
# creates a table/sequence/function in schema public from now on,
# automatically grant $APP_DB_USER these privileges on it" — which is
# exactly what every later `alembic upgrade head` run does, since
# migrations always run as the owner role.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER must be set (it always is — see docker-compose.yml)}"
: "${POSTGRES_DB:?POSTGRES_DB must be set (it always is — see docker-compose.yml)}"
: "${APP_DB_USER:?APP_DB_USER must be set on the postgres service's own environment for this init script to run}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set on the postgres service's own environment for this init script to run}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	DO \$do\$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
	    CREATE ROLE "${APP_DB_USER}" WITH LOGIN PASSWORD '${APP_DB_PASSWORD}'
	      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
	  ELSE
	    ALTER ROLE "${APP_DB_USER}" WITH LOGIN PASSWORD '${APP_DB_PASSWORD}'
	      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
	  END IF;
	END
	\$do\$;

	GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${APP_DB_USER}";
	GRANT USAGE ON SCHEMA public TO "${APP_DB_USER}";
	GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "${APP_DB_USER}";
	GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "${APP_DB_USER}";
	GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "${APP_DB_USER}";
	ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public
	  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "${APP_DB_USER}";
	ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public
	  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "${APP_DB_USER}";
	ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public
	  GRANT EXECUTE ON FUNCTIONS TO "${APP_DB_USER}";
EOSQL

echo "[init] restricted application role \"${APP_DB_USER}\" provisioned."
