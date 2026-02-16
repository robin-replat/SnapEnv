#!/bin/bash
# This script runs automatically on first PostgreSQL container start.
# It creates the test database used by pytest.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE snapenv_test;
EOSQL

echo "✓ Test database 'snapenv_test' created"