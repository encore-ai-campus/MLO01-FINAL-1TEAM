# WEB01 / APP01 Reverse Proxy

## Architecture

Client -> WEB01:80 -> APP01:8001 -> RAG API:8000 -> DB01:5432

## Configuration

| Server | Address | Configuration |
|---|---|---|
| WEB01 | 192.168.0.151:80 | web01.conf |
| APP01 | 192.168.0.158:8001 | app01.conf |
| RAG API | 127.0.0.1:8000 | Docker |

## Deployment

1. Install Nginx on WEB01 and APP01.
2. Copy the corresponding configuration to /etc/nginx/sites-available/.
3. Disable the default site.
4. Enable the new site using a symbolic link.
5. Validate the configuration using nginx -t.
6. Restart Nginx when changing listening addresses.

## Security

- APP01 accepts proxy requests only from WEB01.
- The RAG API retains its loopback binding.
- WEB01 exposes only /api/v1/search/documents.
- Existing APP01 CI/CD configuration remains unchanged.
- Existing UI demo on WEB01 port 8080 remains unchanged.

## Verification

- WEB01 -> APP01 API: HTTP 200
- Client -> WEB01 -> APP01 API: HTTP 200
- Client -> APP01 directly: HTTP 403

## Limitations

- Internal development environment only.
- HTTPS and network-level firewall policies are not yet configured.
- Production UI and authentication integration are pending.
- Deployment is currently manual.
