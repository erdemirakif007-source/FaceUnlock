# Produces a portable release folder. Never includes personal data or credentials.
[CmdletBinding()]
param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $SkipBuild) {
        & .\build.cmd
        if ($LASTEXITCODE -ne 0) { throw 'Credential provider build failed.' }
        & .\build-agent.cmd
        if ($LASTEXITCODE -ne 0) { throw 'Desktop/agent build failed.' }
    }
    $buildRoot = Join-Path $env:LOCALAPPDATA 'FaceUnlockBuild'
    $release = Join-Path $buildRoot ('releases\FaceUnlock-Windows-x64-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    foreach ($required in @((Join-Path $PSScriptRoot 'FaceUnlockCP.dll'),
                           (Join-Path $buildRoot 'dist\FaceUnlockAgent\FaceUnlockAgent.exe'),
                           (Join-Path $buildRoot 'dist\FaceUnlock\FaceUnlock.exe'))) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Missing build output: $required" }
    }
    New-Item -ItemType Directory -Path $release -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $buildRoot 'dist\FaceUnlockAgent') -Destination (Join-Path $release 'agent') -Recurse
    Copy-Item -LiteralPath (Join-Path $buildRoot 'dist\FaceUnlock') -Destination (Join-Path $release 'app') -Recurse
    foreach ($name in @('FaceUnlockCP.dll', 'install.ps1', 'README.md', 'THIRD_PARTY.md', 'SECURITY.md', 'LICENSE')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $release
    }
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'docs')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs') -Destination $release -Recurse
    }
    Set-Content -LiteralPath (Join-Path $release 'FaceUnlock.cmd') -Encoding ASCII -Value '@start "" "%~dp0app\FaceUnlock.exe"'
    $hashes = Get-ChildItem -LiteralPath $release -File -Recurse | Get-FileHash -Algorithm SHA256 |
        ForEach-Object { '{0}  {1}' -f $_.Hash, $_.Path.Substring($release.Length + 1) }
    Set-Content -LiteralPath (Join-Path $release 'SHA256SUMS.txt') -Value $hashes -Encoding UTF8
    Compress-Archive -LiteralPath $release -DestinationPath ($release + '.zip')
    Write-Host "Release package: $release.zip"
} finally { Pop-Location }
