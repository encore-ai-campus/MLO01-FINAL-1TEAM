# Database Infrastructure

## Components

- PostgreSQL 17.12
- pgvector 0.8.7
- pg_textsearch 1.5.1
- Docker Compose
- Timezone: Asia/Seoul

## Deployment

- Target server: DB01
- Deployment directory: /opt/database
- Database: rag_dev
- Container: rag-postgres
- Persistent volume: rag-postgres-data

## Database accounts

- postgres: Local administration
- rag_app: Application service
- rag_pipeline: Data pipeline
- rag_admin: Management workstation
- rag_monitor: PostgreSQL metrics collection

## Security

- Store actual IP addresses and credentials locally.
- Never commit the actual .env file.
- Configure the firewall before publishing the database port.
- Render and validate pg_hba.conf.template before applying it.
- Do not remove or reset the existing database volume.
- Configure TLS before handling sensitive data.

## Current scope

Container provisioning, role creation, firewall migration, existing-schema
migration and TLS configuration are separate tasks.

## Initialize an empty application schema on DB01

`deploy-schema.sh` uses the reviewed `db/init.sql` v1.12 and the pinned image
from `compose.yml`. Run it from a trusted Linux checkout on DB01. It requires
the running `rag-postgres` container, existing login roles, and local socket
authentication for PostgreSQL administration inside the container. Each
invocation uses one host `sudo` invocation.

1. Reserve a maintenance window. Keep APP01 and PIPE01 workloads that access
   `rag_dev` paused. Run `sudo bash infra/database/deploy-schema.sh --check` to
   inspect versions, roles, HBA syntax, and connection counts by role and state.
   Existing application objects require a separate migration. Other client
   sessions, including idle ones, block deployment.
2. If `rag_monitor` appears, identify the actual PostgreSQL Exporter supervisor
   on its host and record whether it is running. The repository's MON01
   inventory leaves the exporter target unset, so verify the deployed endpoint
   and unit or Compose service name. If it was running, pause only that exporter
   with its normal service stop command (`systemctl stop <verified-unit>` or
   `docker compose stop <verified-service>` from its project). Wait for its DB
   sessions to close naturally and rerun the read-only check. Pausing Prometheus
   scrapes alone does not guarantee that pooled database sessions close. If a
   session remains, hold deployment and identify its owning service.
3. Run `sudo bash infra/database/deploy-schema.sh --apply` when the maintenance
   window is ready. The command rechecks the target, backs up `rag_dev` into a
   private directory under `/opt/database`, verifies the checksum and restores
   to a temporary DB. It then applies the schema and checked grants for
   `rag_app` and `rag_pipeline` atomically. Existing `rag_admin` and `rag_monitor`
   grants are retained.
4. After success **or failure**, start any previously running exporter that was
   stopped in step 2 (`systemctl start <verified-unit>` or
   `docker compose start <verified-service>`). Confirm service status and
   `pg_up 1` at the configured metrics endpoint. If registered in Prometheus,
   verify its next scrape is healthy. Resume application clients only after
   resolving the deployment outcome.

Before commit, schema and grant failures roll back together. If a connection
drops at commit or the post-commit check fails, keep writers paused, inspect the
target read-only, and retain `rag_dev.dump`, its checksum, and the phase logs.
For recovery after commit, restore the verified dump into a separate database,
validate it, then arrange an explicit recovery cutover. The script never
overwrites `rag_dev` during recovery.
