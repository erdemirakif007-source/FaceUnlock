# Install a new camera agent beside the old one, then switch the trusted path.
# Run in an elevated PowerShell window using the installed Windows account.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'FaceUnlockAgent'
$configKey = 'HKLM:\SOFTWARE\FaceUnlock'
$targetRoot = Join-Path $env:ProgramFiles 'FaceUnlock'
$built = Join-Path $env:LOCALAPPDATA 'FaceUnlockBuild\dist\FaceUnlockAgent'
$result = Join-Path $env:LOCALAPPDATA 'FaceUnlockBuild\deploy-result.txt'

try {
    Set-Content -LiteralPath $result -Value 'Starting agent update' -Encoding UTF8
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script as Administrator with the installed Windows account.'
    }
    $configured = Get-ItemProperty -LiteralPath $configKey
    if ($configured.UserSid -ne $identity.User.Value) {
        throw 'Administrator account differs from the installed FaceUnlock account.'
    }
    $oldExe = [IO.Path]::GetFullPath($configured.AgentPath)
    $root = [IO.Path]::GetFullPath($targetRoot).TrimEnd('\') + '\'
    if (-not $oldExe.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($oldExe) -ne 'FaceUnlockAgent.exe' -or
        -not (Test-Path -LiteralPath $oldExe)) {
        throw 'Configured FaceUnlockAgent path is unexpected or missing.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $built 'FaceUnlockAgent.exe'))) {
        throw 'Build FaceUnlockAgent first with build-agent.cmd.'
    }

    $newDir = Join-Path $targetRoot ('agent-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
    $newExe = Join-Path $newDir 'FaceUnlockAgent.exe'
    # Copy to a fresh directory so Windows can keep any old DLLs open safely.
    Copy-Item -LiteralPath $built -Destination $newDir -Recurse -Force
    if (-not (Test-Path -LiteralPath $newExe)) {
        throw 'The new agent executable was not copied.'
    }

    $oldAction = (Get-ScheduledTask -TaskName $taskName).Actions
    $switched = $false
    try {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Get-Process FaceUnlockAgent -ErrorAction SilentlyContinue | Stop-Process -Force
        Set-ItemProperty -LiteralPath $configKey -Name AgentPath -Value $newExe
        $switched = $true
        $newAction = New-ScheduledTaskAction -Execute $newExe -WorkingDirectory $newDir
        Set-ScheduledTask -TaskName $taskName -Action $newAction | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 3
        if (-not (Get-Process FaceUnlockAgent -ErrorAction SilentlyContinue)) {
            throw 'The updated agent did not start.'
        }
        Set-Content -LiteralPath $result -Value "Updated FaceUnlockAgent is running: $newExe" -Encoding UTF8
        Write-Host 'FaceUnlockAgent updated and running.'
    } catch {
        $failure = $_.Exception.Message
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Get-Process FaceUnlockAgent -ErrorAction SilentlyContinue | Stop-Process -Force
        if ($switched) {
            Set-ItemProperty -LiteralPath $configKey -Name AgentPath -Value $oldExe
        }
        Set-ScheduledTask -TaskName $taskName -Action $oldAction | Out-Null
        Start-ScheduledTask -TaskName $taskName
        throw "Agent update failed and the old agent was restored: $failure"
    }
} catch {
    Set-Content -LiteralPath $result -Value "Failed: $($_.Exception.Message)" -Encoding UTF8
    throw
}
