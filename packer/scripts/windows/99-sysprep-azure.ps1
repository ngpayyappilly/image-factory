# Generalise per Azure guidance, then wait for sysprep to finish before Packer captures the VM.
Remove-Item -Recurse -Force C:\imagefactory-tools, C:\imagefactory-reports, C:\imagefactory-tests -ErrorAction SilentlyContinue
& "$env:SystemRoot\System32\Sysprep\Sysprep.exe" /oobe /generalize /quiet /quit /mode:vm
while ($true) {
  $state = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Setup\State').ImageState
  if ($state -eq 'IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE') { break }
  Write-Host "ImageState: $state"; Start-Sleep -Seconds 10
}
