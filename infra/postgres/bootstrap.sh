#!/usr/bin/env bash
# brokAId — local Postgres bootstrap.
#
# Idempotent: safe to re-run on a fresh pod or after a config change.
# Puts the data directory on the persistent /app volume so it
# survives pod recycles.

set -euo pipefail

DATA_DIR="/app/.postgres_data"
SUPERVISOR_CONF="/etc/supervisor/conf.d/postgres.conf"
PG_BIN="/usr/lib/postgresql/15/bin"
DB_NAME="brokaid"
DB_ROLE="brokaid"
DB_PASS="${BROKAID_PG_LOCAL_PASSWORD:-brokaid_dev_local}"

log() { printf '[bootstrap] %s\n' "$*"; }

# 1. install postgres if missing
if ! command -v "$PG_BIN/postgres" >/dev/null 2>&1; then
  log "installing postgresql-15…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq postgresql postgresql-contrib
fi

# 2. initdb if empty
mkdir -p "$DATA_DIR"
chown -R postgres:postgres "$DATA_DIR"
chmod 700 "$DATA_DIR"
if [ ! -f "$DATA_DIR/PG_VERSION" ]; then
  log "initdb at $DATA_DIR"
  su - postgres -c "$PG_BIN/initdb -D $DATA_DIR --auth-local=trust --auth-host=md5 --encoding=UTF8 --locale=C.UTF-8" >/dev/null
  # tighten defaults
  sed -i "s/#listen_addresses.*/listen_addresses = 'localhost'/" "$DATA_DIR/postgresql.conf"
  sed -i "s/#port = 5432/port = 5432/" "$DATA_DIR/postgresql.conf"
  sed -i "s|#unix_socket_directories.*|unix_socket_directories = '/var/run/postgresql'|" "$DATA_DIR/postgresql.conf"
fi
mkdir -p /var/run/postgresql
chown postgres:postgres /var/run/postgresql

# 3. supervisor entry
cat > "$SUPERVISOR_CONF" <<EOF
[program:postgres]
command=$PG_BIN/postgres -D $DATA_DIR
user=postgres
autostart=true
autorestart=true
priority=100
stopsignal=INT
stopwaitsecs=30
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/supervisor/postgres.err.log
stdout_logfile=/var/log/supervisor/postgres.out.log
EOF

supervisorctl reread >/dev/null
supervisorctl update >/dev/null
# start / restart to pick up config
supervisorctl restart postgres >/dev/null 2>&1 || supervisorctl start postgres >/dev/null

# 4. wait for readiness, then ensure role + db
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if su - postgres -c "psql -c 'SELECT 1' >/dev/null 2>&1"; then break; fi
  sleep 1
done

if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_ROLE'\"" | grep -q 1; then
  su - postgres -c "psql -c \"CREATE USER $DB_ROLE WITH PASSWORD '$DB_PASS';\"" >/dev/null
fi

if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME'\"" | grep -q 1; then
  su - postgres -c "psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_ROLE;\"" >/dev/null
fi

su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_ROLE;\"" >/dev/null

log "ready — data at $DATA_DIR"
su - postgres -c "psql -d $DB_NAME -c 'SELECT version();'"
