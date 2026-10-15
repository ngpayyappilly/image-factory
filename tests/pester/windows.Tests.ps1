# Pester 5. Tag 'functional' feeds the `goss` gate, tag 'cis' (with a cis-* id) feeds `compliance`.

Describe 'ImageFactory functional' -Tag 'functional' {
  It 'records provenance' {
    (Get-ItemProperty 'HKLM:\SOFTWARE\ImageFactory').Version | Should -Match '^win\d+-\d{8}\.\d+$'
  }
  It 'has Windows Defender real-time protection available' {
    (Get-Service WinDefend).Status | Should -Be 'Running'
  }
  It 'has the EC2Launch or Azure agent installed' {
    (Test-Path "$env:ProgramFiles\Amazon\EC2Launch") -or (Get-Service WindowsAzureGuestAgent -ErrorAction SilentlyContinue) | Should -BeTrue
  }
  It 'has no pending reboot' {
    Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired' | Should -BeFalse
  }
  # Plan gate: activation and domain join must be exercised before promotion. KMS/AVMA activation and
  # domain join happen on a launched instance (boot-and-join smoke test), not in the build VM.
}

Describe 'CIS Level 1 subset' -Tag 'cis' {
  It 'SMBv1 is disabled' -Tag 'cis-smb1' {
    (Get-SmbServerConfiguration).EnableSMB1Protocol | Should -BeFalse
  }
  It 'firewall enabled on all profiles' -Tag 'cis-firewall' {
    (Get-NetFirewallProfile | Where-Object Enabled -ne 'True').Count | Should -Be 0
  }
  It 'Guest account disabled' -Tag 'cis-guest' {
    (Get-LocalUser -Name Guest).Enabled | Should -BeFalse
  }
  It 'LLMNR disabled' -Tag 'cis-llmnr' {
    (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient').EnableMulticast | Should -Be 0
  }
  It 'NTLMv2 only' -Tag 'cis-lmcompat' {
    (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa').LmCompatibilityLevel | Should -Be 5
  }
  It 'logon auditing enabled' -Tag 'cis-audit-logon' {
    (auditpol /get /subcategory:"Logon" /r | ConvertFrom-Csv).'Inclusion Setting' | Should -Be 'Success and Failure'
  }
}
