# APP01 RAG API pull deployment

The RAG CI workflow keeps the PR checks and publishes only on a successful
`dev` push. It builds and checks the same image it pushes under
`ghcr.io/encore-ai-campus/mlo01-final-1team/rag-api:sha-<40-character-commit>`.
The workflow summary records the commit and the registry digest after the
provenance step. APP01 rechecks the CI run and publishing job, matches the SHA
tag to the approved digest, verifies provenance for the repository, workflow
and `refs/heads/dev`, and pulls by digest.

## Install and approve a release

Perform these steps in an approved APP01 maintenance window. Use a reviewed,
pinned checkout or transfer the reviewed files. The service never downloads
deployment scripts. Install Docker Compose and Buildx and a GitHub CLI that
supports the attestation flags. Confirm registry access and configure
attestation lookup as root if authentication is required. Keep registry and
GitHub credentials separate from the service environment, with read access only
and root-only storage. Package visibility is set separately from repository
visibility and must go through its own approval.

The existing Compose project must be `app` with service and container
`rag-api`. Keep `/opt/app/compose.yml`, its root-only `.env`, and
`127.0.0.1:8000` in place. From the reviewed checkout:

```sh
sudo install -d -m 0700 /opt/app/cd /opt/app/cd-state
sudo install -m 0600 infra/app/pull-deploy.py infra/app/smoke_test.py infra/app/verify_database.py /opt/app/cd/
sudo install -m 0644 infra/app/systemd/app01-rag-deploy.service infra/app/systemd/app01-rag-deploy.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Confirm that the publishing run succeeded, review the full source commit and
copy its registry digest from the workflow summary. As an administrator, create a
root-owned `/opt/app/cd/approved.json` with mode `0600`, using an atomic rename
from a file in the same directory. Do not derive its values automatically from
the latest dev commit. Its contents are only:

```json
{"sha":"<40 lowercase hex characters>","digest":"sha256:<64 lowercase hex characters>"}
```

The timer may run without an approval file. In that case it leaves the app alone.
It also skips an already active digest or a digest recorded as failed. Replace
the approval file when approving a different release. A tag that moves to a
different digest requires a new review and approval.

## Check, deploy, and enable automation

```sh
sudo /usr/bin/python3 /opt/app/cd/pull-deploy.py --check
sudo systemctl start app01-rag-deploy.service
sudo journalctl -u app01-rag-deploy.service -n 40 --no-pager
# Enable only after the first reviewed deployment succeeds.
sudo systemctl enable --now app01-rag-deploy.timer
```

The check pulls the approved image and uses a temporary unpublished Compose
container to check the DB contract in a read-only transaction with the current
`.env`. It does not switch the live service. A deploy records the current
working image locally before replacing the one service and verifies the
loopback OpenAPI, document search and anonymous QA response twice. Search may
return zero documents. Keyword-only mode with `EMBEDDING_UNAVAILABLE` is valid.
A replacement uses the existing port, so allow a short interruption.

## Rollback and history

On a failed switch, the helper restores the previous local image and performs
the same checks. The pending switch is durable, so a subsequent run recovers it
before acting on a new approval. If rollback fails, pending state is retained
and subsequent invocations retry recovery. Keep the tagged `rag-api:cd-rollback-*`
images available and exclude them from cleanup. If the helper detects an
unexpected running image, inspect the manual change before proceeding.

To roll back by hand, stop the timer to pause new updates and run:

```sh
sudo systemctl disable --now app01-rag-deploy.timer
sudo /usr/bin/python3 /opt/app/cd/pull-deploy.py --rollback
```

If a pending switch exists, this command prioritizes recovery. State and history
are in `/opt/app/cd-state/state.json` and `history.jsonl`. The history identifies
the candidate CI run, SHA and digest when available. Keep the failure list to
prevent repeat rollbacks. To retry a recorded digest after investigation,
disable the timer, wait for the service to finish, and remove only that digest's
`failed` entry from `state.json` using an atomic root-owned replacement. Then run
a manual check and deployment. A separate migration is required when the
database contract changes. The helper performs no migrations.
