# OfficeManLab Private PKI

## Architecture

- Root CA: OfficeManLab Root CA
- CA key: RSA 4096, AES-256 encrypted
- CA validity: 3650 days
- Server key: RSA 3072
- Server certificate validity: 365 days
- Signature algorithm: SHA-256 with RSA
- CRL validity: 30 days
- CDP: http://pki.officemanlab.com:8081/root-ca.crl

## Certificate Deployment

- Server: WEB01
- Hostname: upmutok.officemanlab.com
- Certificate: /etc/nginx/ssl/upmutok.crt
- Private key: /etc/nginx/ssl/upmutok.key
- Private key permission: 600
- CRL: /var/www/pki/root-ca.crl

## Certificate Issuance

Run from the secure CA working directory.

    openssl ca -config openssl-ca.cnf -in upmutok.csr -out upmutok-new.crt -extfile upmutok.ext -extensions server_cert -days 365 -notext

## Certificate Revocation

Verify the target certificate serial number before revocation.

    openssl ca -config openssl-ca.cnf -revoke target.crt -crl_reason superseded

## CRL Renewal

    openssl ca -config openssl-ca.cnf -gencrl -out root-ca.crl.pem
    openssl crl -in root-ca.crl.pem -outform DER -out root-ca.crl

Verify the CRL signature before deployment.

    openssl crl -in root-ca.crl.pem -noout -verify -CAfile root-ca.crt

Deploy the DER CRL to WEB01 and replace the published file atomically.

Renew the CRL before its expiration and after certificate revocation.

## Verification

    openssl verify -CAfile root-ca.crt -CRLfile root-ca.crl.pem -crl_check -purpose sslserver -verify_hostname upmutok.officemanlab.com upmutok-new.crt

    curl.exe -I https://upmutok.officemanlab.com/

HTTP 404 is expected until the frontend is deployed.

## Security Policy

- Never commit private keys or CA passwords.
- Never commit the CA database or serial state files.
- Keep encrypted CA backups in a secure location.
- Distribute only the public Root CA certificate to clients.
- Keep HTTP port 80 disabled.
- Use port 8081 exclusively for CRL distribution.
- Do not bypass certificate validation.

## Operational Limitations

- CRL renewal is currently manual.
- CRL expiration monitoring is not yet configured.
- CA backup and recovery procedures require further implementation.
- Public domain ownership has not been verified.
- Internal WEB01-to-APP01 traffic still uses HTTP.

## Change History

### 2026-11-26

- Created the private Root CA.
- Issued and deployed the WEB01 TLS certificate.
- Established the CRL distribution endpoint.
- Reissued the server certificate with CDP.
- Revoked the superseded certificate.
- Verified HTTPS and document search API connectivity.
