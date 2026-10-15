# Produces gate evidence (goss.json, compliance.json, sbom.json, trivy.json). Pass/fail is decided
# later by `imagefactory gate-ingest`. Exit 1 = evidence incomplete (gates stay unmet).
$ErrorActionPreference = 'Continue'
$R = 'C:\imagefactory-reports'; $T = 'C:\imagefactory-tools'
New-Item -ItemType Directory -Force $R, $T | Out-Null
$rc = 0

function Get-Verified($name, $url, $sha) {
  if (-not $url -or -not $sha) { Write-Host "gates: no pinned URL/SHA256 for $name"; return $false }
  $dl = Join-Path $T "$name.zip"
  Invoke-WebRequest -Uri $url -OutFile $dl -UseBasicParsing
  if ((Get-FileHash $dl -Algorithm SHA256).Hash -ne $sha.ToUpper()) { Write-Host "gates: checksum mismatch for $name"; return $false }
  Expand-Archive $dl -DestinationPath $T -Force
  return $true
}

# Pester -> the same JSON shape the Linux (goss) evidence uses.
Install-Module Pester -MinimumVersion 5.5 -Force -Scope AllUsers -SkipPublisherCheck | Out-Null
function Export-Pester($tag, $file) {
  $cfg = New-PesterConfiguration
  $cfg.Run.Path = 'C:\imagefactory-tests'; $cfg.Run.PassThru = $true
  $cfg.Filter.Tag = $tag; $cfg.Output.Verbosity = 'None'
  $res = Invoke-Pester -Configuration $cfg
  $items = @($res.Tests | ForEach-Object {
    $m = @{}; if ($_.Tag) { $m.id = ($_.Tag | Where-Object { $_ -like 'cis-*' } | Select-Object -First 1) }
    @{ title = $_.ExpandedName; successful = ($_.Result -eq 'Passed'); meta = $m }
  })
  @{ results = $items; summary = @{ 'test-count' = $items.Count; 'failed-count' = @($items | Where-Object { -not $_.successful }).Count } } |
    ConvertTo-Json -Depth 6 | Set-Content (Join-Path $R $file)
}
Export-Pester 'functional' 'goss.json'
Export-Pester 'cis' 'compliance.json'

if (Get-Verified 'syft' $env:SYFT_URL $env:SYFT_SHA256) {
  & "$T\syft.exe" 'dir:C:\' -o "cyclonedx-json=$R\sbom.json" --exclude './imagefactory-*/**' --exclude './Windows/WinSxS/**'
} else { $rc = 1 }
if (Get-Verified 'trivy' $env:TRIVY_URL $env:TRIVY_SHA256) {
  & "$T\trivy.exe" rootfs C:\ --scanners vuln,secret --skip-dirs 'C:\imagefactory-tools,C:\imagefactory-reports,C:\imagefactory-tests' --format json --output "$R\trivy.json" --quiet
} else { $rc = 1 }
exit $rc
