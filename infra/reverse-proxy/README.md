# Reverse Proxy Infrastructure

## Architecture

Client -> WEB01:443 -> APP01:8001 -> RAG API:8000

## WEB01

- IP: 192.168.0.151
- FQDN: upmutok.officemanlab.com
- HTTPS: TCP 443
- TLS: 1.2 and 1.3
- HTTP port 80: Disabled
- Server tokens: Off
- Certificate authority: OfficeManLab Root CA
- API route: /api/v1/search/documents
- Default response: HTTP 404

## APP01

- IP: 192.168.0.158
- Reverse proxy port: 8001
- Allowed source: WEB01 (192.168.0.151)
- Backend: 127.0.0.1:8000
- Direct client access: Denied

## PKI Distribution

- Hostname: pki.officemanlab.com
- Port: TCP 8081
- Protocol: HTTP
- CRL path: /root-ca.crl
- Other paths: HTTP 404

## Configuration Files

- nginx.conf: WEB01 global Nginx configuration
- web01.conf: HTTPS reverse proxy
- app01.conf: Internal API reverse proxy
- pki.conf: CRL distribution endpoint

## Verification

- Nginx configuration test: Passed
- HTTPS certificate validation: Passed
- Certificate revocation check: Passed
- HTTPS document search API: Passed
- TCP 80 connectivity: Failed as expected
- TCP 443 connectivity: Passed
- CRL distribution: HTTP 200

## Operational Limitations

- Frontend integration is pending.
- Legacy demo service remains on port 8080.
- WEB01-to-APP01 traffic uses HTTP.
- Network-level firewall policies are pending.
- Configuration deployment is currently manual.

## Related Documentation

- ../dns/README.md
- ../pki/README.md
