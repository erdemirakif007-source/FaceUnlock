# Run in an elevated PowerShell window belonging to the same Windows user.
# Usage: .\install.ps1
# After re-enrolling the face: .\install.ps1 -RefreshData
# Remove: .\install.ps1 -Remove
[CmdletBinding()]
param([switch]$Remove, [switch]$RefreshData)

$ErrorActionPreference = 'Stop'
$resultFile = Join-Path $PSScriptRoot 'install-result.txt'
function Write-InstallStatus([string]$Message) {
    Set-Content -LiteralPath $resultFile -Value $Message -Encoding UTF8
}
trap {
    $line = $_.InvocationInfo.ScriptLineNumber
    Write-InstallStatus "Failed at line ${line}: $($_.Exception.Message)"
    break
}
Write-InstallStatus 'Starting'
$guid = '{FC607A37-AE23-4B3E-8035-A1055740B3AA}'
$providerKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$guid"
$comKey = "HKLM:\SOFTWARE\Classes\CLSID\$guid"
$configKey = 'HKLM:\SOFTWARE\FaceUnlock'
$targetDir = Join-Path $env:ProgramFiles 'FaceUnlock'
$targetDll = Join-Path $targetDir 'FaceUnlockCP.dll'
$agentHome = Join-Path $env:LOCALAPPDATA 'FaceUnlock'
$legacyHome = Join-Path $env:USERPROFILE 'FaceLock'
if (Test-Path -LiteralPath (Join-Path $legacyHome 'config.json')) { $agentHome = $legacyHome }
$agentBuild = Join-Path $env:LOCALAPPDATA 'FaceUnlockBuild\dist\FaceUnlockAgent'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'agent\FaceUnlockAgent.exe')) {
    $agentBuild = Join-Path $PSScriptRoot 'agent'
}
$appBuild = Join-Path $env:LOCALAPPDATA 'FaceUnlockBuild\dist\FaceUnlock'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'app\FaceUnlock.exe')) {
    $appBuild = Join-Path $PSScriptRoot 'app'
}
$agentDir = Join-Path $targetDir 'agent'
$agentExe = Join-Path $agentDir 'FaceUnlockAgent.exe'
$dataDir = Join-Path $targetDir 'data'
$taskName = 'FaceUnlockAgent'

# The agent and everything it loads must sit where this user cannot write;
# the provider accepts signals only from $agentExe.
function Test-AgentData {
    $modelHashes = @{
        'yunet.onnx' = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
        'sface.onnx' = '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'
        'hand_landmarker.task' = 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'
        'face_landmarker.task' = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
    }
    foreach ($name in $modelHashes.Keys) {
        $path = Join-Path $agentHome "models\$name"
        if (-not (Test-Path -LiteralPath $path) -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $modelHashes[$name]) {
            throw "Model verification failed: $name. Use the panel to prepare models again."
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $agentHome 'my_face.npy'))) {
        throw 'Enroll your face in the panel before installation.'
    }
}

function Copy-ProtectedAgentData {
    New-Item -ItemType Directory -Path (Join-Path $dataDir 'models') -Force | Out-Null
    foreach ($name in @('yunet.onnx', 'sface.onnx', 'hand_landmarker.task', 'face_landmarker.task')) {
        Copy-Item -LiteralPath (Join-Path $agentHome "models\$name") -Destination (Join-Path $dataDir 'models') -Force
    }
    Copy-Item -LiteralPath (Join-Path $agentHome 'my_face.npy') -Destination $dataDir -Force
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'PowerShell must be started as Administrator using the same Windows account.'
}
$existingConfig = Get-ItemProperty -LiteralPath $configKey -ErrorAction SilentlyContinue
if ($existingConfig -and $existingConfig.UserSid -ne $identity.User.Value) {
    throw 'Use the same Windows account that installed FaceUnlock.'
}

$lsaSource = @'
using System;
using System.Runtime.InteropServices;
public static class FaceUnlockLsa {
    [StructLayout(LayoutKind.Sequential)]
    public struct Attributes {
        public uint Length;
        public IntPtr RootDirectory, ObjectName;
        public uint AttributesValue;
        public IntPtr SecurityDescriptor, SecurityQualityOfService;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct UnicodeString {
        public ushort Length, MaximumLength;
        public IntPtr Buffer;
    }
    [DllImport("advapi32.dll")]
    public static extern uint LsaOpenPolicy(IntPtr system, ref Attributes attributes,
        uint desiredAccess, out IntPtr policy);
    [DllImport("advapi32.dll", EntryPoint="LsaStorePrivateData")]
    public static extern uint Store(IntPtr policy, ref UnicodeString key,
        ref UnicodeString value);
    [DllImport("advapi32.dll", EntryPoint="LsaStorePrivateData")]
    public static extern uint Delete(IntPtr policy, ref UnicodeString key,
        IntPtr value);
    [DllImport("advapi32.dll")]
    public static extern uint LsaClose(IntPtr policy);
}
'@
Add-Type -TypeDefinition $lsaSource

function Set-FaceUnlockSecret([Security.SecureString]$Password) {
    $attributes = [FaceUnlockLsa+Attributes]::new()
    $policy = [IntPtr]::Zero
    $status = [FaceUnlockLsa]::LsaOpenPolicy([IntPtr]::Zero, [ref]$attributes, 0x20, [ref]$policy)
    if ($status -ne 0) { throw "LsaOpenPolicy failed: $status" }
    $keyPtr = [Runtime.InteropServices.Marshal]::StringToHGlobalUni('L$FaceUnlockPassword')
    $key = [FaceUnlockLsa+UnicodeString]::new()
    $key.Buffer = $keyPtr
    $key.Length = [uint16]('L$FaceUnlockPassword'.Length * 2)
    $key.MaximumLength = [uint16]($key.Length + 2)
    $secretPtr = [IntPtr]::Zero
    try {
        if ($null -eq $Password) {
            $status = [FaceUnlockLsa]::Delete($policy, [ref]$key, [IntPtr]::Zero)
        } else {
            if ($Password.Length -lt 1 -or $Password.Length -gt 512) {
                throw 'Password length must be between 1 and 512 characters.'
            }
            $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
            $secret = [FaceUnlockLsa+UnicodeString]::new()
            $secret.Buffer = $secretPtr
            $secret.Length = [uint16]($Password.Length * 2)
            $secret.MaximumLength = [uint16]($secret.Length + 2)
            $status = [FaceUnlockLsa]::Store($policy, [ref]$key, [ref]$secret)
        }
        if ($null -eq $Password -and $status -eq 0xC0000034) { $status = 0 }
        if ($status -ne 0) { throw "LsaStorePrivateData failed: $status" }
    } finally {
        if ($secretPtr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
        }
        [Runtime.InteropServices.Marshal]::FreeHGlobal($keyPtr)
        [void][FaceUnlockLsa]::LsaClose($policy)
    }
}

if ($Remove) {
    Remove-Item -LiteralPath $providerKey -Recurse -Force -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    try { Set-FaceUnlockSecret $null } catch { Write-Warning $_ }
    Remove-Item -LiteralPath $comKey -Recurse -Force -ErrorAction SilentlyContinue
    $userTileKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI\UserTile'
    $saved = Get-ItemProperty -LiteralPath $configKey -ErrorAction SilentlyContinue
    if ($saved -and $saved.UserSid -and $saved.OriginalUserTileProvider) {
        $current = (Get-Item -LiteralPath $userTileKey).GetValue($saved.UserSid, $null)
        if ($current -eq $guid) {
            New-ItemProperty -LiteralPath $userTileKey -Name $saved.UserSid -Value $saved.OriginalUserTileProvider -PropertyType String -Force | Out-Null
        }
    }
    $logonKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI'
    if ($saved -and $saved.OriginalLastLoggedOnProvider -and
        (Get-ItemProperty -LiteralPath $logonKey).LastLoggedOnProvider -eq $guid) {
        New-ItemProperty -LiteralPath $logonKey -Name LastLoggedOnProvider -Value $saved.OriginalLastLoggedOnProvider -PropertyType String -Force | Out-Null
    }
    Remove-Item -LiteralPath $configKey -Recurse -Force -ErrorAction SilentlyContinue
    if ($saved -and $saved.LegacyFaceLockWasEnabled -eq 1) {
        Enable-ScheduledTask -TaskName 'FaceLock' -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName 'FaceLock' -ErrorAction SilentlyContinue
    }
    $shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\FaceUnlock.lnk'
    Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
    Write-Host 'FaceUnlock provider unregistered; PIN and password sign-in remain available.'
    return
}

if ($RefreshData) {
    if (-not $existingConfig) { throw 'Install FaceUnlock before refreshing enrollment.' }
    Test-AgentData
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    try { Copy-ProtectedAgentData } finally { Start-ScheduledTask -TaskName $taskName }
    Write-InstallStatus 'Data refreshed'
    Write-Host 'Enrollment and models copied; agent restarted.'
    return
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'FaceUnlockCP.dll'))) {
    throw 'Build FaceUnlockCP.dll first by running build.cmd.'
}
if (-not (Test-Path -LiteralPath (Join-Path $agentBuild 'FaceUnlockAgent.exe'))) {
    throw 'Build the agent first by running build-agent.cmd.'
}
if (-not (Test-Path -LiteralPath (Join-Path $appBuild 'FaceUnlock.exe'))) {
    throw 'Build the desktop panel with build-agent.cmd, or use the full release ZIP.'
}
Test-AgentData
$passwordless = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device' -ErrorAction SilentlyContinue).DevicePasswordLessBuildVersion
if ($null -ne $passwordless -and $passwordless -ne 0) {
    throw 'The Windows Hello-only sign-in setting must be off for third-party credential providers.'
}
$password = Read-Host 'Enter this Windows account password (never the PIN)' -AsSecureString
if ($null -eq $password) { throw 'Password entry was cancelled.' }
# Check before anything is copied, so a mistyped entry leaves the install untouched.
if ($password.Length -lt 1 -or $password.Length -gt 512) {
    throw 'Password was empty or too long; nothing was changed. Run the script again.'
}

New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'FaceUnlockCP.dll') -Destination $targetDll -Force
& cipher.exe /D $targetDll | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not decrypt installed DLL for Windows SYSTEM account.' }
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Get-Process FaceUnlockAgent -ErrorAction SilentlyContinue | Stop-Process -Force
# Keep previous versions intact if Windows still holds their files open.
if (Test-Path -LiteralPath $agentDir) {
    $agentDir = Join-Path $targetDir ('agent-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
    $agentExe = Join-Path $agentDir 'FaceUnlockAgent.exe'
}
Copy-Item -LiteralPath $agentBuild -Destination $agentDir -Recurse -Force
Copy-ProtectedAgentData
$appDir = Join-Path $targetDir ('app-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
Copy-Item -LiteralPath $appBuild -Destination $appDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination $targetDir -Force
$shortcutPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\FaceUnlock.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $appDir 'FaceUnlock.exe'
$shortcut.WorkingDirectory = $appDir
$shortcut.Save()
Write-InstallStatus 'Files copied'

$action = New-ScheduledTaskAction -Execute $agentExe -WorkingDirectory $agentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Write-InstallStatus 'Scheduled task registered'

Set-FaceUnlockSecret $password
$password.Dispose()
Write-InstallStatus 'Password stored'
New-Item -Path $configKey -Force | Out-Null
$legacyTask = Get-ScheduledTask -TaskName 'FaceLock' -ErrorAction SilentlyContinue
if ($legacyTask -and $legacyTask.State -ne 'Disabled') {
    New-ItemProperty -Path $configKey -Name LegacyFaceLockWasEnabled -Value 1 -PropertyType DWord -Force | Out-Null
    Stop-ScheduledTask -TaskName 'FaceLock' -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName 'FaceLock' | Out-Null
}
New-ItemProperty -Path $configKey -Name UserSid -Value $identity.User.Value -PropertyType String -Force | Out-Null
New-ItemProperty -Path $configKey -Name AgentPath -Value $agentExe -PropertyType String -Force | Out-Null
New-Item -Path (Join-Path $comKey 'InprocServer32') -Force | Out-Null
Set-Item -Path (Join-Path $comKey 'InprocServer32') -Value $targetDll
New-ItemProperty -Path (Join-Path $comKey 'InprocServer32') -Name ThreadingModel -Value 'Apartment' -PropertyType String -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
# Register last, after all prerequisites and secret storage have succeeded.
New-Item -Path $providerKey -Force | Out-Null
Set-Item -Path $providerKey -Value 'FaceUnlock'
Write-InstallStatus 'Installed'
Write-Host 'FaceUnlock installed. Restart Windows before testing; PIN sign-in remains available.'
