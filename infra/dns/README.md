# OfficeManLab AD and DNS Infrastructure

## Active Directory

- Server: DC01
- OS: Windows Server 2019
- IP: 192.168.0.161
- Forest Root: ad.officemanlab.com
- NetBIOS: OFFICEMANLAB
- Forest Functional Level: Windows Server 2016
- Domain Functional Level: Windows Server 2016

## DNS Zones

| Zone | Purpose |
|---|---|
| ad.officemanlab.com | Active Directory |
| officemanlab.com | Internal services |

## DNS Records

| Record | Type | IP Address |
|---|---|---|
| upmutok.officemanlab.com | A | 192.168.0.151 |
| pki.officemanlab.com | A | 192.168.0.151 |

## DNS Forwarders

- 168.126.63.1
- 8.8.8.8

## Change History

### 2026-11-26

- Removed the legacy ad.team01.com forest.
- Created the ad.officemanlab.com forest.
- Verified SYSVOL and NETLOGON shares.
- Passed Connectivity, Advertising, SysVolCheck and NetLogons tests.
- Verified internal and external DNS resolution.
- Created the internal service DNS zone.

## Precautions

- Public domain ownership has not been verified.
- The DNS zones are intended for internal development.
- Internal clients must use the internal DNS server.
- DC01 is currently a single point of failure.
- AD application authentication is not yet integrated.
