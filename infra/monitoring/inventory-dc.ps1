$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version
Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress
Get-Service -Name NTDS,DNS,Netlogon,Kdc,windows_exporter -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType
Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort -Unique
Get-NetUDPEndpoint | Select-Object LocalAddress, LocalPort -Unique
