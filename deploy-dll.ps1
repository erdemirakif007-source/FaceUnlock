$ErrorActionPreference = 'Stop'
$result = Join-Path $PSScriptRoot 'deploy-result.txt'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run as Administrator with the installed Windows account.'
    }
    $configKey = 'HKLM:\SOFTWARE\FaceUnlock'
    $sid = (Get-ItemProperty -LiteralPath $configKey).UserSid
    if ($sid -ne $identity.User.Value) {
        throw 'Administrator account differs from the installed FaceUnlock account.'
    }
    $agentPath = (Get-ItemProperty -LiteralPath $configKey).AgentPath
    if (-not $agentPath -or -not (Test-Path -LiteralPath $agentPath)) {
        throw 'The provider only trusts the packaged agent; run build-agent.cmd and install.ps1 instead.'
    }
    $source = Join-Path $PSScriptRoot 'FaceUnlockCP.dll'
    $target = 'C:\Program Files\FaceUnlock\FaceUnlockCP.dll'
    $backup = 'C:\ProgramData\FaceUnlock\FaceUnlockCP.previous.dll'
    New-Item -ItemType Directory -Path (Split-Path $backup) -Force | Out-Null
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Copy-Item -LiteralPath $source -Destination $target -Force
    & cipher.exe /D $target | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not decrypt installed DLL for Windows SYSTEM account.' }
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
        throw 'DLL hash mismatch'
    }
    $userTileKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI\UserTile'
    $tile = Get-Item -LiteralPath $userTileKey
    $previous = $tile.GetValue($sid, $null)
    $original = (Get-ItemProperty -LiteralPath $configKey -ErrorAction SilentlyContinue).OriginalUserTileProvider
    if (-not $original -and $previous) {
        New-ItemProperty -LiteralPath $configKey -Name OriginalUserTileProvider -Value $previous -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $userTileKey -Name $sid -Value '{FC607A37-AE23-4B3E-8035-A1055740B3AA}' -PropertyType String -Force | Out-Null
    $logonKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI'
    $last = (Get-ItemProperty -LiteralPath $logonKey).LastLoggedOnProvider
    if ($last -and -not (Get-ItemProperty -LiteralPath $configKey).OriginalLastLoggedOnProvider) {
        New-ItemProperty -LiteralPath $configKey -Name OriginalLastLoggedOnProvider -Value $last -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $logonKey -Name LastLoggedOnProvider -Value '{FC607A37-AE23-4B3E-8035-A1055740B3AA}' -PropertyType String -Force | Out-Null
    Set-Content -LiteralPath $result -Value 'DLL updated; FaceUnlock selected for this user' -Encoding UTF8
} catch {
    Set-Content -LiteralPath $result -Value "Failed: $($_.Exception.Message)" -Encoding UTF8
    throw
}
