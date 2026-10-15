# Remove build tooling and evidence, then generalise via EC2Launch v2 (the supported path on AWS
# Windows Server 2022+). --shutdown stops the instance so Packer snapshots it.
Remove-Item -Recurse -Force C:\imagefactory-tools, C:\imagefactory-reports, C:\imagefactory-tests -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName 'ImageFactory build WinRM HTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
& "$env:ProgramFiles\Amazon\EC2Launch\ec2launch.exe" sysprep --shutdown --clean
