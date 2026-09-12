# infra/postgres

Local Postgres for brokAId development.

Phase 0: a Postgres 15 cluster is stood up inside the build container.
Its data directory lives on the pod's persistent ext4 volume at
`/app/.postgres_data` (gitignored) and is supervised via
`/etc/supervisor/conf.d/postgres.conf`. The database `brokaid` and
role `brokaid` are created at first boot.

Do **not** place the data directory on the container's `overlay`
filesystem (e.g. `/var/lib/postgresql/data`) — that path is wiped on
pod recycle. `/app` is a real mounted volume in this environment
(`findmnt -T /app` → `/dev/nvme0n2[/app]  ext4`), so putting the data
dir under `/app/.postgres_data` gives us pod-recycle persistence at
zero cost.

## Bootstrap

`bootstrap.sh` idempotently:

1. Installs `postgresql-15` if `psql` is missing.
2. Runs `initdb` at `/app/.postgres_data` if it is empty.
3. Writes the supervisor entry at `/etc/supervisor/conf.d/postgres.conf`.
4. Reloads supervisor and creates the `brokaid` role + database if
   they do not exist.

Run it once after a fresh pod:

```
bash infra/postgres/bootstrap.sh
```

For CI and other developer machines, a Docker-based cluster will be
provided in a later phase. `init.sql` is the seed script; it will
contain the ledger schema from phase 1 onwards.

## Local connection

```
postgresql://brokaid:brokaid_dev_local@localhost:5432/brokaid
```

The password above is a **local development** value and never
represents a real credential. Production credentials live only in
environment variables provisioned outside this repository.
