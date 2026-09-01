# ============================================================
# COF Research Assistant - CREST (Docker) one-click installer
# ------------------------------------------------------------
# Prereqs: Windows 10 2004+ / Windows 11, admin rights
# Steps:   enable WSL2 -> install/start Docker Desktop ->
#          build cof-crest image -> verify crest inside container
# NOTE: If WSL2 is enabled for the first time, a system reboot
#       may be required midway. Re-run this script afterwards;
#       completed steps are skipped automatically.
# ============================================================
$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- Self-elevate: relaunch via UAC prompt if not elevated ----
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $myPath = $MyInvocation.MyCommand.Path
    Write-Output 'Admin rights required: a UAC prompt will appear, please click Yes.'
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $myPath)
        exit $LASTEXITCODE
    } catch {
        Write-Output "Elevation failed: $($_.Exception.Message)"
        exit 1
    }
}

function Step { Write-Output "`n=== $args ===" }

function Wsl-StatusText {
    $s = cmd.exe /c "wsl.exe --status 2>nul"
    return ($s -join "`n")
}

function Test-WslReady {
    $s = Wsl-StatusText
    return ($s -match 'WSL2|kernel version')
}

function Wait-DockerReady([int]$TimeoutSec = 300) {
    $docker = "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $docker)) { return $false }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        & $docker info *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

Step '0. Check WSL2 status'
# Clean up leftovers from previous stuck runs:
#  - winget's StagePackageAsync can hang forever on some networks
#  - a zombie deployment operation blocks the AppX pipeline, so an older
#    instance of this script must be killed and AppXSvc restarted
Get-Process winget -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$cutoff = (Get-Date).AddMinutes(-10)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'setup_crest_docker' -and $_.ProcessId -ne $PID -and $_.CreationDate -lt $cutoff } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output "killed orphan setup process $($_.ProcessId)" }
try { Restart-Service AppXSvc -Force -ErrorAction SilentlyContinue } catch { Write-Output 'AppXSvc restart skipped' }
Start-Sleep -Seconds 3
if (-not (Test-WslReady)) {
    Write-Output 'WSL2 not ready. Enabling Windows features (WSL + VirtualMachinePlatform)...'
    cmd.exe /c 'dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart' | Out-String | Write-Output
    cmd.exe /c 'dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart' | Out-String | Write-Output
    # Install the modern WSL app if wsl.exe is still the old inbox stub.
    # MSI-first: wsl.2.x.x64.msi installs wsl.exe + WslService + kernel via
    # plain msiexec, avoiding the AppX deployment pipeline entirely (that
    # pipeline can hang forever behind a stuck winget StagePackageAsync op).
    # Fallbacks: local MSIX bundle via Add-AppxPackage, then winget.
    $wslVer = cmd.exe /c "wsl.exe --version 2>nul"
    if (($wslVer -join "`n") -notmatch 'WSL version') {
        $msi = Join-Path $env:TEMP 'wsl.2.7.12.0.x64.msi'
        $bundle = Join-Path $env:TEMP 'Microsoft.WSL_2.7.12.msixbundle'
        if (Test-Path $msi) {
            Write-Output 'Local WSL MSI found; installing silently (msiexec, no AppX)...'
            cmd.exe /c "msiexec.exe /i `"$msi`" /qn /norestart" | Out-String | Write-Output
        } elseif (Test-Path $bundle) {
            Write-Output 'Local WSL app bundle found; installing via Add-AppxPackage...'
            Add-AppxPackage -Path $bundle -ForceUpdateFromAnyVersion
        } else {
            Write-Output 'Old inbox wsl.exe stub detected. Installing modern WSL via winget...'
            cmd.exe /c 'winget install --id Microsoft.WSL -e --accept-package-agreements --accept-source-agreements --disable-interactivity' | Out-String | Write-Output
        }
    }
    # Update WSL kernel: local MSI first (wsl --update pulls the same file
    # from GitHub and can stall on the same network path)
    $kernelMsi = Join-Path $env:TEMP 'wsl.2.7.12.0.x64.msi'
    if (Test-Path $kernelMsi) {
        Write-Output 'Local WSL kernel MSI found; installing silently...'
        cmd.exe /c "msiexec.exe /i `"$kernelMsi`" /qn /norestart" | Out-String | Write-Output
    } else {
        cmd.exe /c "wsl.exe --update" 2>&1 | Out-String | Write-Output
    }
}
if (Test-WslReady) {
    Write-Output 'WSL2 is ready.'
} else {
    Write-Output 'WSL2 needs a system reboot to take effect.'
    Write-Output 'Save your work, reboot, then run this script again.'
    Write-Output '(If wsl.exe is still the old stub after reboot: run "winget install --id Microsoft.WSL -e" as admin first.)'
    exit 1
}

Step '1. Check Docker Desktop'
$dockerExe = "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
if (-not (Test-Path $dockerExe)) {
    Write-Output 'Docker Desktop not found. Installing via winget (~1GB, please wait)...'
    cmd.exe /c 'winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements --disable-interactivity' | Out-String | Write-Output
}

Step '2. Start Docker Desktop and wait for the engine'
$dd = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dd) { Start-Process $dd } else { Write-Output 'Docker Desktop.exe not found; start it manually from the Start menu.' }
if (Wait-DockerReady 300) {
    Write-Output 'Docker engine is ready.'
} else {
    Write-Output 'Docker engine not ready within 5 minutes. Finish Docker Desktop first-run setup (accept terms), then re-run this script.'
    exit 1
}

Step '3. Build the cof-crest image (conda-forge crest + xtb)'
$dockerfile = Join-Path $ScriptDir 'docker\crest\Dockerfile'
if (-not (Test-Path $dockerfile)) {
    Write-Output "Dockerfile not found: $dockerfile"
    exit 1
}
& $dockerExe image inspect cof-crest:latest *> $null
if ($LASTEXITCODE -ne 0) {
    & $dockerExe build -t cof-crest:latest (Split-Path $dockerfile)
} else {
    Write-Output 'cof-crest image already exists, skipping build (rebuild: docker rmi cof-crest:latest).'
}

Step '4. Verify CREST inside the container'
& $dockerExe run --rm cof-crest:latest crest --version
if ($LASTEXITCODE -ne 0) {
    Write-Output 'CREST container verification failed; check the build log above.'
    exit 1
}

Write-Output "`nCREST (Docker) setup completed."
Write-Output '  - The CREST engine in the DFT page (auto retrieve low-energy conformers) will now use this container;'
Write-Output '  - First container call has a few seconds of startup overhead, which is normal.'
