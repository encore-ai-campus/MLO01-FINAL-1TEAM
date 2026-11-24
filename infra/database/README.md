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

## Security

- Store actual IP addresses and credentials locally.
- Never commit the actual .env file.
- Configure the firewall before publishing the database port.
- Render and validate pg_hba.conf.template before applying it.
- Do not remove or reset the existing database volume.
- Configure TLS before handling sensitive data.

## Current scope

This configuration is subject to CI validation only.
Automated deployment, role provisioning, firewall migration,
schema migration and TLS configuration are separate tasks.
The extension SQL file is not executed automatically.
