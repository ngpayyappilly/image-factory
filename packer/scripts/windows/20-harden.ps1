$ErrorActionPreference = 'Stop'
# CIS Level 1 starter subset. Extend via DSC (plan: PowerShell + DSC) as the Windows track matures;
# every item here has a matching Pester check in tests/pester/windows.Tests.ps1 (tag: cis).

Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart | Out-Null
Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force

Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True

Disable-LocalUser -Name Guest -ErrorAction SilentlyContinue

# Password policy / lockout via secedit
$inf = Join-Path $env:TEMP 'imagefactory-sec.inf'
@"
[Unicode]
Unicode=yes
[System Access]
MinimumPasswordLength = 14
PasswordComplexity = 1
MaximumPasswordAge = 365
LockoutBadCount = 5
LockoutDuration = 15
ResetLockoutCount = 15
[Version]
signature="`$CHICAGO`$"
Revision=1
"@ | Set-Content $inf
secedit /configure /db "$env:TEMP\imagefactory.sdb" /cfg $inf /areas SECURITYPOLICY | Out-Null

# Audit policy
auditpol /set /subcategory:"Logon" /success:enable /failure:enable | Out-Null
auditpol /set /subcategory:"Account Lockout" /failure:enable | Out-Null

# Disable LLMNR; require NTLMv2 only
New-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' -Force | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' -Name EnableMulticast -Value 0 -Type DWord
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name LmCompatibilityLevel -Value 5 -Type DWord

# Provenance on the machine (fleet inventory: which VM runs which build)
New-Item 'HKLM:\SOFTWARE\ImageFactory' -Force | Out-Null
foreach ($kv in @{ Version = $env:IMAGE_VERSION; Baseline = $env:BASELINE_VERSION; Commit = $env:GIT_COMMIT; Flavor = $env:FLAVOR }.GetEnumerator()) {
  Set-ItemProperty 'HKLM:\SOFTWARE\ImageFactory' -Name $kv.Key -Value $kv.Value
}
