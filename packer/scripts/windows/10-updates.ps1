$ErrorActionPreference = 'Stop'
# Install all pending updates. Re-run via windows-restart until clean in the template if needed.
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
Install-Module PSWindowsUpdate -Force -Scope AllUsers | Out-Null
Get-WindowsUpdate -AcceptAll -Install -IgnoreReboot -MicrosoftUpdate | Out-String | Write-Host
