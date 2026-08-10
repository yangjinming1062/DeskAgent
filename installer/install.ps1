# DeskAgent Agent installer (Windows / PowerShell 5.1+).
#
# 6-stage payload release. Tauri DeskAgent-Setup.exe is the GUI shell that
# spawns this script; the script's job is to install Python (if needed),
# copy the bundled runner binary, desktop app, and skills
# into the user's $DESKAGENT_HOME (and the platform-canonical desktop install
# location).
#
# Protocol:
#   powershell -File install.ps1 -Manifest                 → emit manifest JSON
#   powershell -File install.ps1 -Stage NAME -Json         → run a single stage
#
# Payload locations are passed via DESKAGENT_BUNDLE_* env vars (set by the Tauri
# installer) or via the matching --bundled-*-dir parameters (for dev/test).
# When both are present, env wins.

[CmdletBinding()]
param(
    [switch]$Manifest,
    [string]$Stage,
    [switch]$Json,
    [switch]$NonInteractive,
    [string]$DeskAgentHome,
    [string]$BundledRunnerDir,
    [string]$BundledDesktopDir,
    [string]$BundledSkillsDir,
    [string]$BundledVoicesDir,
    [string]$BundledOnboardingAudioDir,
    [string]$InstallerFormat
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$InformationPreference = 'Continue'
$ErrorActionPreference = "Stop"
$ProtocolVersion = 2
$ScriptName = "install.ps1"
$RunnerWheelGlob = "desk_agent-*.whl"
$DefaultDesktopFormat = "nsis"
$PythonVersion = "3.13"
$PythonFallbackVersions = @("3.12", "3.14", "3.11")

# --- resolve paths: env var > param > default ------------------------------

if (-not $DeskAgentHome) {
    if ($env:DESKAGENT_HOME) { $DeskAgentHome = $env:DESKAGENT_HOME }
    else { $DeskAgentHome = Join-Path $env:LOCALAPPDATA "deskagent" }
}
if (-not $BundledRunnerDir -and $env:DESKAGENT_BUNDLED_RUNNER_DIR) { $BundledRunnerDir = $env:DESKAGENT_BUNDLED_RUNNER_DIR }
if (-not $BundledDesktopDir -and $env:DESKAGENT_BUNDLED_DESKTOP_DIR) { $BundledDesktopDir = $env:DESKAGENT_BUNDLED_DESKTOP_DIR }
if (-not $BundledSkillsDir -and $env:DESKAGENT_BUNDLED_SKILLS_DIR) { $BundledSkillsDir = $env:DESKAGENT_BUNDLED_SKILLS_DIR }
if (-not $BundledVoicesDir -and $env:DESKAGENT_BUNDLED_VOICES_DIR) { $BundledVoicesDir = $env:DESKAGENT_BUNDLED_VOICES_DIR }
if (-not $BundledOnboardingAudioDir -and $env:DESKAGENT_BUNDLED_ONBOARDING_AUDIO_DIR) { $BundledOnboardingAudioDir = $env:DESKAGENT_BUNDLED_ONBOARDING_AUDIO_DIR }
if (-not $InstallerFormat) {
    if ($env:DESKAGENT_INSTALLER_FORMAT) { $InstallerFormat = $env:DESKAGENT_INSTALLER_FORMAT }
    else { $InstallerFormat = $DefaultDesktopFormat }
}

# --- output helpers ---------------------------------------------------------

function Emit-Manifest {
    @"
{"protocol_version": $ProtocolVersion, "stages": [
  {"name": "welcome", "title": "\u51c6\u5907\u5b89\u88c5", "category": "setup", "needs_user_input": false},
  {"name": "install-python", "title": "\u5b89\u88c5 Python \u8fd0\u884c\u65f6", "category": "prereqs", "needs_user_input": false},
  {"name": "unpack-runner", "title": "\u5b89\u88c5 DeskAgent \u8fd0\u884c\u5668", "category": "payload", "needs_user_input": false},
  {"name": "unpack-desktop", "title": "\u5b89\u88c5 DeskAgent \u684c\u9762\u5e94\u7528", "category": "payload", "needs_user_input": false},
  {"name": "install-skills", "title": "\u5b89\u88c5\u5185\u7f6e\u6280\u80fd", "category": "payload", "needs_user_input": false},
  {"name": "write-config", "title": "\u5199\u5165\u914d\u7f6e\u6587\u4ef6", "category": "finalize", "needs_user_input": false}
]}
"@
}

function Escape-JsonString([string]$s) {
    if ($null -eq $s) { return "" }
    return $s.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n').Replace("`r", '\r').Replace("`t", '\t')
}

function Emit-StageOk([string]$stage, [bool]$skipped = $false, [string]$reason = "") {
    if ($skipped -and $reason) {
        Write-Output "{`"ok`": true, `"stage`": `"$stage`", `"skipped`": true, `"reason`": `"" + (Escape-JsonString $reason) + "`"}"
    } else {
        Write-Output "{`"ok`": true, `"stage`": `"$stage`"}"
    }
}

function Emit-StageErr([string]$stage, [string]$reason) {
    Write-Output "{`"ok`": false, `"stage`": `"$stage`", `"reason`": `"" + (Escape-JsonString $reason) + "`"}"
}

# --- Python installation helpers --------------------------------------------

function Install-Uv {
    $managedUv = Join-Path $DeskAgentHome "bin\uv.exe"
    if (Test-Path $managedUv) {
        $script:UvCmd = $managedUv
        return $true
    }

    $binDir = Join-Path $DeskAgentHome "bin"
    if (-not (Test-Path $binDir)) {
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    }

    try {
        $env:UV_INSTALL_DIR = $binDir
        $psHostExe = Get-PowerShellHostExe
        & $psHostExe -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null

        if (-not (Test-Path $managedUv)) {
            return $false
        }
        $script:UvCmd = $managedUv
        return $true
    } catch {
        return $false
    }
}

function Get-PowerShellHostExe {
    try {
        $hostExe = (Get-Process -Id $PID).Path
        if ($hostExe -and (Test-Path $hostExe)) {
            $leaf = Split-Path $hostExe -Leaf
            if ($leaf -match '^(?i:powershell|pwsh)\.exe$') { return $hostExe }
        }
    } catch { }
    foreach ($candidate in @("powershell", "pwsh")) {
        $cmd = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }
    return "powershell"
}

function Test-Python {
    if (-not $script:UvCmd) {
        if (-not (Install-Uv)) { return $false }
    }

    $candidates = @($PythonVersion) + $PythonFallbackVersions
    foreach ($ver in $candidates) {
        if (-not $ver) { continue }
        try {
            $found = & $script:UvCmd python find $ver 2>$null
            if ($found) {
                $script:PythonVersion = $ver
                return $true
            }
        } catch { }
    }

    # Cold cache — install the preferred version and re-check just that one.
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $script:UvCmd python install $PythonVersion 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        $found = & $script:UvCmd python find $PythonVersion 2>$null
        if ($found) {
            return $true
        }
    } catch {
        $ErrorActionPreference = $prevEAP
    }

    return $false
}

# --- stage 1: welcome -------------------------------------------------------

function Stage-Welcome {
    $bin = Join-Path $DeskAgentHome "bin"
    $skills = Join-Path $DeskAgentHome "skills"
    $logs = Join-Path $DeskAgentHome "logs"

    foreach ($d in @($bin, $skills, $logs)) {
        if (-not (Test-Path $d)) {
            New-Item -ItemType Directory -Force -Path $d | Out-Null
        }
    }

    if (-not (Test-Path $DeskAgentHome -PathType Container)) {
        Emit-StageErr "welcome" "could not create DESKAGENT_HOME: $DeskAgentHome"
        return 1
    }

    $marker = Join-Path $DeskAgentHome ".deskagent-bootstrap-complete"
    $isReinstall = Test-Path $marker

    $escHome = Escape-JsonString $DeskAgentHome
    Write-Output "{`"ok`": true, `"stage`": `"welcome`", `"data`": {`"deskagent_home`": `"$escHome`", `"is_reinstall`": $($isReinstall.ToString().ToLower())}}"
    return 0
}

# --- stage 2: install-python ------------------------------------------------

function Stage-InstallPython {
    if (Test-Python) {
        $escVer = Escape-JsonString $script:PythonVersion
        Write-Output "{`"ok`": true, `"stage`": `"install-python`", `"data`": {`"version`": `"$escVer`"}}"
        return 0
    }

    Emit-StageErr "install-python" "Python $PythonVersion is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run."
    return 1
}

# --- stage 3: unpack-runner -------------------------------------------------

function Stage-UnpackRunner {
    if (-not $script:UvCmd -or -not $script:PythonVersion) {
        if (-not (Test-Python)) {
            Emit-StageErr "unpack-runner" "Python runtime not available (uv or Python missing)"
            return 1
        }
    }

    if (-not $BundledRunnerDir) {
        Emit-StageErr "unpack-runner" "--BundledRunnerDir (or DESKAGENT_BUNDLED_RUNNER_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledRunnerDir -PathType Container)) {
        Emit-StageErr "unpack-runner" "bundled runner dir not found: $BundledRunnerDir"
        return 1
    }

    $wheel = Get-ChildItem -Path $BundledRunnerDir -Filter $RunnerWheelGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $wheel) {
        Emit-StageErr "unpack-runner" "wheel not found in $BundledRunnerDir"
        return 1
    }

    $runnerDir = Join-Path $DeskAgentHome "runner"
    if (-not (Test-Path $runnerDir)) { New-Item -ItemType Directory -Force -Path $runnerDir | Out-Null }

    # Copy server.py alongside the wheel (not inside it)
    $serverSrc = Join-Path $BundledRunnerDir "server.py"
    if (Test-Path $serverSrc) { Copy-Item -Force $serverSrc (Join-Path $runnerDir "server.py") }

    # Create venv (requires install-python stage to have completed)
    $venvDir = Join-Path $runnerDir ".venv"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $venvOutput = & $script:UvCmd venv $venvDir --python $script:PythonVersion --clear 2>&1
    if ($LASTEXITCODE) { $ErrorActionPreference = $prevEAP; Emit-StageErr "unpack-runner" "uv venv failed: $($venvOutput -join ' | ')"; return 1 }

    # Install wheel into venv (installs wheel + deps in one shot)
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"
    $pipOutput = & $script:UvCmd pip install --python $pythonExe $wheel.FullName 2>&1
    if ($LASTEXITCODE) { 
        # Retry with a custom or default domestic mirror to mitigate common network issues
        $pypiIndex = $env:DESKAGENT_PYPI_INDEX_URL
        if (-not $pypiIndex) { $pypiIndex = $env:PIP_INDEX_URL }
        if (-not $pypiIndex) { $pypiIndex = "https://mirrors.aliyun.com/pypi/simple/" }
        $pipOutputRetry = & $script:UvCmd pip install --python $pythonExe $wheel.FullName --index-url $pypiIndex 2>&1
        if ($LASTEXITCODE) {
            $ErrorActionPreference = $prevEAP; Emit-StageErr "unpack-runner" "uv pip install failed: $($pipOutputRetry -join ' | ')"; return 1 
        }
    }

    # No post-install smoke: build_client.ps1 already gates the wheel
    # behind pytest tests/test_startup_imports.py (and the full runner
    # test suite via the same hook), so a broken venv can't reach users
    # through this installer. install.ps1 stays simple.

    # Clean up old PyInstaller binary if present
    $oldBin = Join-Path (Join-Path $DeskAgentHome "bin") "deskagent-runner.exe"
    if (Test-Path $oldBin) { Remove-Item -Force $oldBin }

    # Copy bundled Piper voices (installer/payload/voices/) into the models
    # directory so local TTS works offline on day 1. Each voice needs both
    # ``.onnx`` and ``.onnx.json`` — a partial copy is useless to Piper.
    # Content-based copy so future voice additions only need a payload
    # drop, no install-script edit.
    $voiceCount = 0
    if ($BundledVoicesDir -and (Test-Path $BundledVoicesDir -PathType Container)) {
        $voicesTarget = Join-Path $DeskAgentHome "models\piper"
        if (-not (Test-Path $voicesTarget)) { New-Item -ItemType Directory -Force -Path $voicesTarget | Out-Null }

        $onnxFiles = Get-ChildItem -Path $BundledVoicesDir -Filter "*.onnx" -File -ErrorAction SilentlyContinue
        foreach ($onnx in $onnxFiles) {
            $jsonPath = Join-Path $BundledVoicesDir ($onnx.Name + ".json")
            if (Test-Path $jsonPath) {
                Copy-Item -Force $onnx.FullName (Join-Path $voicesTarget $onnx.Name)
                Copy-Item -Force $jsonPath (Join-Path $voicesTarget $jsonPath.Name)
            }
        }
        $voiceCount = (Get-ChildItem -Path $voicesTarget -Filter "*.onnx" -File -ErrorAction SilentlyContinue).Count
    }

    # Copy bundled onboarding guidance audio — language subdirs (zh\, en\, …) map
    # 1:1 to $DeskAgentHome\audio\onboarding\<lang>\.
    $audioCount = 0
    if ($BundledOnboardingAudioDir -and (Test-Path $BundledOnboardingAudioDir -PathType Container)) {
        Get-ChildItem -Path $BundledOnboardingAudioDir -Directory | ForEach-Object {
            $audioTarget = Join-Path (Join-Path (Join-Path $DeskAgentHome "audio") "onboarding") $_.Name
            if (-not (Test-Path $audioTarget)) { New-Item -ItemType Directory -Force -Path $audioTarget | Out-Null }
            Copy-Item -Recurse -Force (Join-Path $_.FullName "*") $audioTarget
        }
        $audioRoot = Join-Path (Join-Path $DeskAgentHome "audio") "onboarding"
        $audioCount = (Get-ChildItem -Path $audioRoot -Filter "*.mp3" -Recurse -File -ErrorAction SilentlyContinue).Count
    }

    $size = $wheel.Length
    $escVenv = Escape-JsonString $venvDir
    $escWheel = Escape-JsonString $wheel.Name
    Write-Output "{`"ok`": true, `"stage`": `"unpack-runner`", `"data`": {`"venv`": `"$escVenv`", `"wheel`": `"$escWheel`", `"size_bytes`": $size, `"voices_copied`": $voiceCount, `"onboarding_audio_copied`": $audioCount}}"
    return 0
}

# --- stage 4: unpack-desktop ------------------------------------------------

function Stage-UnpackDesktop {
    if (-not $BundledDesktopDir) {
        Emit-StageErr "unpack-desktop" "--BundledDesktopDir (or DESKAGENT_BUNDLED_DESKTOP_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledDesktopDir -PathType Container)) {
        Emit-StageErr "unpack-desktop" "bundled desktop dir not found: $BundledDesktopDir"
        return 1
    }

    # Locate the artifact by format.
    $artifact = $null
    switch ($InstallerFormat) {
        "nsis" { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        "msi"  { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.msi"  -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        "zip"  { $artifact = Get-ChildItem -Path $BundledDesktopDir -Filter "*.zip"  -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
        default {
            Emit-StageErr "unpack-desktop" "unknown desktop format: $InstallerFormat"
            return 1
        }
    }

    if (-not $artifact) {
        Emit-StageErr "unpack-desktop" "no desktop artifact found in $BundledDesktopDir (format=$InstallerFormat)"
        return 1
    }
    $artifactPath = $artifact.FullName

    switch ($InstallerFormat) {
        "nsis" {
            # NSIS /S = silent install; /D sets install dir. No console window
            # because Tauri's installer process already owns the visible window.
            $localPrograms = Join-Path $env:LOCALAPPDATA "Programs"
            $installDir = Join-Path $localPrograms "DeskAgent"
            if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Force -Path $installDir | Out-Null }
            $proc = Start-Process -FilePath $artifactPath `
                                  -ArgumentList @("/S", "/D=$installDir") `
                                  -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0) {
                Emit-StageErr "unpack-desktop" "NSIS installer exited with code $($proc.ExitCode)"
                return 1
            }
            $escPath = Escape-JsonString $installDir
            Write-Output "{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"nsis`"}}"
            return 0
        }
        "msi" {
            # msiexec /qn = quiet, no UI; REBOOT=ReallySuppress prevents reboot prompts.
            $proc = Start-Process -FilePath "msiexec.exe" `
                                  -ArgumentList @("/i", $artifactPath, "/qn", "REBOOT=ReallySuppress") `
                                  -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0) {
                Emit-StageErr "unpack-desktop" "msiexec exited with code $($proc.ExitCode)"
                return 1
            }
            $localPrograms = Join-Path $env:LOCALAPPDATA "Programs"
            $installDir = Join-Path $localPrograms "DeskAgent"
            $escPath = Escape-JsonString $installDir
            Write-Output "{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"msi`"}}"
            return 0
        }
        "zip" {
            # Extract ZIP to $DESKAGENT_HOME\apps\DeskAgent (NSIS-style layout) — desktop
            # is then launched from DeskAgent.exe inside. Matches the install_root
            # path bootstrap.rs::resolve_deskagent_desktop_exe expects on Windows.
            $dest = Join-Path $DeskAgentHome "apps\DeskAgent"
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Expand-Archive -Path $artifactPath -DestinationPath $dest -Force
            $escPath = Escape-JsonString $dest
            Write-Output "{`"ok`": true, `"stage`": `"unpack-desktop`", `"data`": {`"installed_path`": `"$escPath`", `"format`": `"zip`"}}"
            return 0
        }
    }
    return 1
}

# --- stage 5: install-skills ------------------------------------------------

function Stage-InstallSkills {
    if (-not $BundledSkillsDir) {
        Emit-StageErr "install-skills" "--BundledSkillsDir (or DESKAGENT_BUNDLED_SKILLS_DIR) is required"
        return 1
    }
    if (-not (Test-Path $BundledSkillsDir -PathType Container)) {
        Emit-StageErr "install-skills" "bundled skills dir not found: $BundledSkillsDir"
        return 1
    }

    # Respect the .no-bundled-skills marker.
    $noSkillsMarker = Join-Path $DeskAgentHome ".no-bundled-skills"
    if (Test-Path $noSkillsMarker) {
        Emit-StageOk "install-skills" $true "user opted out via .no-bundled-skills"
        return 0
    }

    $skillsDir = Join-Path $DeskAgentHome "skills"
    if (-not (Test-Path $skillsDir)) { New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null }

    # robocopy /MIR mirrors (preserves user-added dirs/files that don't
    # collide with the bundle). Exit codes 0-7 are success; >=8 is failure.
    & robocopy $BundledSkillsDir $skillsDir /MIR /NFL /NDL /NJH /NJS /NP /R:0 /W:0 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Emit-StageErr "install-skills" "robocopy skills failed: exit $LASTEXITCODE"
        return 1
    }

    $bundledCount = (Get-ChildItem -Path $skillsDir -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Output "{`"ok`": true, `"stage`": `"install-skills`", `"data`": {`"bundled_count`": $bundledCount}}"
    return 0
}

# --- stage 6: write-config --------------------------------------------------

function Stage-WriteConfig {
    # Config is no longer shipped as a file — the Desktop owns it and pushes
    # to the Runner over the WS protocol. This stage now only writes the
    # bootstrap-complete marker that the macOS launcher fast-path checks.
    $marker = Join-Path $DeskAgentHome ".deskagent-bootstrap-complete"
    Set-Content -Path $marker -Value "" -NoNewline

    $escMarker = Escape-JsonString $marker
    Write-Output "{`"ok`": true, `"stage`": `"write-config`", `"data`": {`"marker`": `"$escMarker`"}}"
    return 0
}

# --- dispatch ---------------------------------------------------------------

if ($Manifest) {
    Emit-Manifest
    exit 0
}

if (-not $Stage) {
    Write-Error "error: -Stage NAME is required (or pass -Manifest)"
    exit 2
}

# Runs a stage scriptblock: JSON frames emitted via Write-Output go to
# stdout, and the stage's `return` value (the LAST element of the result
# array) is the exit code.
function Run-Stage([scriptblock]$fn) {
    $result = @(& $fn)
    $code = if ($result.Count -gt 0) { $result[-1] } else { 0 }
    $result | Select-Object -First ($result.Count - 1) | ForEach-Object { Write-Output $_ }
    if ($code) { exit $code }
}

switch ($Stage) {
    "welcome"        { Run-Stage { Stage-Welcome } }
    "install-python" { Run-Stage { Stage-InstallPython } }
    "unpack-runner"  { Run-Stage { Stage-UnpackRunner } }
    "unpack-desktop" { Run-Stage { Stage-UnpackDesktop } }
    "install-skills" { Run-Stage { Stage-InstallSkills } }
    "write-config"   { Run-Stage { Stage-WriteConfig } }
    default {
        Emit-StageErr $Stage "unknown stage"
        exit 1
    }
}
