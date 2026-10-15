<powershell>
# EC2Launch v2 user-data: enable WinRM over HTTPS with a self-signed cert so Packer can connect.
# Build-time only: the image is sysprepped afterwards and this listener does not ship.
$ErrorActionPreference = 'Stop'
$cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Get-ChildItem WSMan:\Localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
New-Item -Path WSMan:\LocalHost\Listener -Transport HTTPS -Address * -CertificateThumbPrint $cert.Thumbprint -Force | Out-Null
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $false
New-NetFirewallRule -DisplayName 'ImageFactory build WinRM HTTPS' -Direction Inbound -Protocol TCP -LocalPort 5986 -Action Allow | Out-Null
Restart-Service WinRM
</powershell>
