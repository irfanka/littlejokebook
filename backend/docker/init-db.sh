#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER little_jokebook WITH PASSWORD 'little_jokebook';
    CREATE DATABASE little_jokebook OWNER little_jokebook;
EOSQL
