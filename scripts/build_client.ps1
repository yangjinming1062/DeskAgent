# Build the DeskAgent client installer for Windows.
#
# Single entry point that orchestrates:
#   1. uv build wheel → runner/dist/deskagent-agent-*.whl
#   2. electron-builder → client/release/DeskAgent-{ver}-win-x64-nsis.exe
#   3. Stage payload (runner wheel + desktop + skills + config) to installer/payload/
#   4. Patch tauri.conf.json so bundle.resources contains the current host's
#      desktop artifact (Tauri 2 fails on missing resources).
#   5. Tauri build → installer\src-tauri\target\release\bundle\nsis\DeskAgent-Setup_*_x64-setup.exe
#   6. Restore tauri.conf.json (git state preserved).
#   7. Copy the final installer to the output directory.
#
# Backend (Docker) is NOT built here — it has its own CI/repo path.
#
# Usage:
#   powershell -File scripts\build_client.ps1 -Version 0.16.0
#
# Parameters:
#   -Version X.Y.Z      Required. Written into desktop + installer package.json
#                       and runner/pyproject.toml.
#   -SkipRunner         Don't build runner wheel (use existing dist/deskagent-agent-*.whl).
#   -SkipDesktop        Don't build desktop (use existing release/DeskAgent-*-nsis.exe).
#   -SignTool PATH      signtool.exe path (defaults to first in PATH).
#   -CertThumbprint TH  Code-sign certificate thumbprint.
#   -OutputDir DIR      Output directory for the final installer. Default: release\.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$SkipRunner,
    [switch]$SkipDesktop,

    [string]$SignTool = "signtool.exe",
    [string]$CertThumbprint,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..").Path

# Load shared signing + manifest helpers (Sign-Manifest, New-RunnerManifest,
# Resolve-UpdateSigningKey).
. (Join-Path $ScriptDir 'lib/UpdateManifest.ps1')

if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "release" }

$RunnerWheelGlob = "deskagent-agent-*.whl"
$DesktopPnpmTarget = "dist:win:nsis"
$DesktopArtifactGlob = "DeskAgent-${Version}-win-*.exe"
$DesktopFormat = "nsis"
$TauriBundleDir = "nsis"

# --- helpers ----------------------------------------------------------------

function Set-Version([string]$v) {
    Write-Output "==> Writing version $v to package.json/pyproject.toml"
    $desktopPkg = Join-Path $RepoRoot "client\package.json"
    $installerPkg = Join-Path $RepoRoot "installer\package.json"
    $tauriConf = Join-Path $RepoRoot "installer\src-tauri\tauri.conf.json"
    $cargoToml = Join-Path $RepoRoot "installer\src-tauri\Cargo.toml"
    $runnerPyproject = Join-Path $RepoRoot "runner\pyproject.toml"

    foreach ($p in @($desktopPkg, $installerPkg, $tauriConf)) {
        $text = Get-Content $p -Raw -Encoding UTF8
        $text = [regex]::Replace($text, '^(\s*)"version"\s*:\s*"[^"]*"', "`$1`"version`": `"$v`"", "Multiline")
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($p, $text, $utf8NoBom)
    }

    foreach ($p in @($cargoToml, $runnerPyproject)) {
        $text = Get-Content $p -Raw -Encoding UTF8
        $text = [regex]::Replace($text, '^version = "[^"]+"', "version = `"$v`"", "Multiline")
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($p, $text, $utf8NoBom)
    }
}

function Stage-Payload {
    Write-Output "==> Staging payload in installer\payload\"
    $payloadRunner = Join-Path $RepoRoot "installer\payload\runner"
    $payloadDesktop = Join-Path $RepoRoot "installer\payload\client"
    if (Test-Path $payloadRunner) { Remove-Item -Recurse -Force $payloadRunner }
    if (Test-Path $payloadDesktop) { Remove-Item -Recurse -Force $payloadDesktop }
    New-Item -ItemType Directory -Force -Path $payloadRunner | Out-Null
    New-Item -ItemType Directory -Force -Path $payloadDesktop | Out-Null

    $runnerWheel = Get-ChildItem -Path (Join-Path $RepoRoot "runner\dist") -Filter $RunnerWheelGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $runnerWheel) {
        throw "no wheel matching '$RunnerWheelGlob' in runner\dist\ (build runner first)"
    }
    $runnerDst = Join-Path $payloadRunner $runnerWheel.Name
    Copy-Item -Force $runnerWheel.FullName $runnerDst
    # Also copy server.py into the payload so install scripts deploy it to $DESKAGENT_HOME/runner/
    Copy-Item -Force (Join-Path $RepoRoot "runner\server.py") (Join-Path $payloadRunner "server.py")

    # Junction skills/install scripts into the payload dir.
    # Junctions are Windows-native symbolic links to directories; hardlinks
    # work for the .sh / .ps1 files. All sources are inside the repo's
    # installer/ subtree so relative resolution at bundle time stays simple.
    $skillsLink = Join-Path $RepoRoot "installer\payload\skills"
    $installShLink = Join-Path $RepoRoot "installer\payload\install.sh"
    $installPs1Link = Join-Path $RepoRoot "installer\payload\install.ps1"
    foreach ($p in @($skillsLink, $installShLink, $installPs1Link)) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }

    $cmdOutput = & cmd /c "mklink /J `"$skillsLink`" `"$RepoRoot\installer\skills`"" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "mklink skills failed: $cmdOutput" }
    # install.sh and install.ps1 are files, not directories — use hardlinks.
    $cmdOutput = & cmd /c "mklink /H `"$installShLink`" `"$RepoRoot\installer\install.sh`"" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "mklink install.sh failed: $cmdOutput" }
    $cmdOutput = & cmd /c "mklink /H `"$installPs1Link`" `"$RepoRoot\installer\install.ps1`"" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "mklink install.ps1 failed: $cmdOutput" }

    $size = (Get-Item (Join-Path $payloadRunner $runnerWheel.Name)).Length
    Write-Output "    runner: $size bytes"
    Write-Output "    desktop: $((Get-ChildItem $payloadDesktop | Select-Object -ExpandProperty Name) -join ' ')"
    Write-Output "    install scripts: install.sh, install.ps1"
}

function Build-UpdateZip {
    <#
    .SYNOPSIS
      Build the DeskAgent-{ver}-update.zip artifact consumed by the desktop
      client's self-updater.

    .DESCRIPTION
      The update zip carries BOTH the inner Electron desktop artifacts
      (electron-builder output) AND the Python runner wheel + server.py, so a
      single update refreshes both halves of the user-side agent. The
      runner-half is laid out under `runner/` inside the zip; the backend's
      `_extract_archive_entries` mirrors the layout to
      `updates/versions/{ver}/runner/`.

      Skills ship INSIDE the wheel (as `package_data` declared in
      runner/pyproject.toml); the desktop never sees a separate skills tar.

      Output: {OutputDir}/DeskAgent-{ver}-update.zip, containing:
        - desktop artifacts (exe / dmg / zip + blockmap)
        - latest.yml / latest-mac.yml (re-signed defensively)
        - latest-runner.yml (signed by New-RunnerManifest)
        - app-update.yml (electron-builder output; the URL placeholder is
          ignored by the desktop client, which resolves the host at runtime)
        - manifest.json (uploaded alongside the binaries so the backend can
          validate version consistency)
        - runner/deskagent-agent-{ver}-py3-none-any.whl
        - runner/server.py
    #>
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$DesktopReleaseDir,
        [Parameter(Mandatory)][string]$RunnerWheelPath,
        [Parameter(Mandatory)][string]$ServerPyPath,
        [Parameter(Mandatory)][string]$OutputDir
    )

    if (-not (Test-Path $DesktopReleaseDir)) {
        throw "Desktop release dir not found: $DesktopReleaseDir — run `pnpm dist` first."
    }
    if (-not (Test-Path $RunnerWheelPath)) {
        throw "Runner wheel not found: $RunnerWheelPath"
    }
    if (-not (Test-Path $ServerPyPath)) {
        throw "server.py not found: $ServerPyPath"
    }

    Write-Output "==> Building update zip for $Version"

    $stageDir = Join-Path ([IO.Path]::GetTempPath()) "deskagent-update-stage-$Version-$PID"
    if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
    New-Item -ItemType Directory -Path $stageDir | Out-Null

    try {
        # 1. Copy only current-version artifacts and update manifests into
        #    staging. Exclude win-unpacked/, builder debug files, and stale
        #    artifacts from prior builds.
        $versionPrefix = "DeskAgent-$Version"
        Get-ChildItem -Path $DesktopReleaseDir -File | Where-Object {
            $_.Name -like "$versionPrefix*" -or
            $_.Name -match '^latest.*\.yml$' -or
            $_.Name -eq 'app-update.yml'
        } | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $stageDir -Force
        }

        # 2. Stage runner wheel + server.py under runner/.
        $runnerStage = Join-Path $stageDir 'runner'
        New-Item -ItemType Directory -Path $runnerStage -Force | Out-Null
        Copy-Item -Force $RunnerWheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath))
        Copy-Item -Force $ServerPyPath (Join-Path $runnerStage 'server.py')

        # 3. Resolve the private key once (used for all per-platform re-signs
        # and for New-RunnerManifest).
        $keyPath = Resolve-UpdateSigningKey

        # 4. Defensive re-sign of any per-platform latest*.yml whose
        # `signature` field is missing. Build-UpdateZip is the canonical signer
        # (see Sign-Manifest in lib/UpdateManifest.ps1); this catches the case
        # where electron-builder emitted an unsigned manifest on a cross-host
        # build.
        foreach ($name in @('latest.yml', 'latest-mac.yml')) {
            $manifestPath = Join-Path $stageDir $name
            if (-not (Test-Path $manifestPath)) { continue }
            $raw = Get-Content -Raw $manifestPath
            if ($raw -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') { continue }
            Write-Output "  re-signing $name (signature was missing)"
            Sign-Manifest -ManifestPath $manifestPath -KeyPath $keyPath
        }

        # 5. Produce + sign latest-runner.yml. The desktop main process reads
        # this BEFORE the restart, fetches the wheel + server.py locally, and
        # only AFTER both have staged + verified does it offer the user
        # "Restart now".
        New-RunnerManifest `
            -Version $Version `
            -WheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath)) `
            -ServerPyPath (Join-Path $runnerStage 'server.py') `
            -OutDir $stageDir `
            -KeyPath $keyPath | Out-Null

        # 6. Pick the canonical desktop exe (Windows NSIS) to record in
        # manifest.json so the backend's _extract_archive_entries finds it
        # without ambiguity.
        $desktopExe = Get-ChildItem $stageDir -Filter 'DeskAgent-*-win-*.exe' -File | Select-Object -First 1
        $manifest = [ordered]@{
            version        = $Version
            desktop_path   = if ($desktopExe) { $desktopExe.Name } else { $null }
            runner_wheel   = "runner/$(Split-Path -Leaf $RunnerWheelPath)"
            server_py      = 'runner/server.py'
            manifests      = @('latest.yml', 'latest-mac.yml', 'latest-runner.yml')
        }
        ($manifest | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $stageDir 'manifest.json') -NoNewline

        # 7. Zip up.
        $zipPath = Join-Path $OutputDir "DeskAgent-${Version}-update.zip"
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
        Add-Type -AssemblyName 'System.IO.Compression.FileSystem'
        [IO.Compression.ZipFile]::CreateFromDirectory($stageDir, $zipPath)
        Write-Output "==> Update zip: $zipPath ($((Get-Item $zipPath).Length) bytes)"
    } finally {
        Remove-Item -Recurse -Force $stageDir -ErrorAction SilentlyContinue
    }
}

function Patch-TauriConfig {
    $conf = Join-Path $RepoRoot "installer\src-tauri\tauri.conf.json"
    $bak = "$conf.build_client.bak"
    Copy-Item -Force $conf $bak

    $desktopDir = Join-Path $RepoRoot "installer\payload\client"
    $desktopFile = Get-ChildItem -Path $desktopDir -File | Select-Object -First 1
    if (-not $desktopFile) { throw "no desktop artifact in $desktopDir" }
    $desktopRel = "..\payload\client\$($desktopFile.Name)"

    Write-Output "==> Patching ${conf}: bundle.resources += $desktopRel"
    $json = Get-Content $conf -Raw -Encoding UTF8 | ConvertFrom-Json
    $resources = @($json.bundle.resources) + $desktopRel
    $json.bundle.resources = $resources
    $jsonStr = ($json | ConvertTo-Json -Depth 100)
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($conf, $jsonStr, $utf8NoBom)
}

function Restore-TauriConfig {
    $conf = Join-Path $RepoRoot "installer\src-tauri\tauri.conf.json"
    $bak = "$conf.build_client.bak"
    if (Test-Path $bak) {
        Write-Output "==> Restoring $conf"
        Move-Item -Force $bak $conf
    }
}

# --- main -------------------------------------------------------------------

Push-Location $RepoRoot
try {
    Set-Version $Version

    # 1. Build runner.
    if (-not $SkipRunner) {
        Write-Output "==> Building runner (uv build wheel → dist\deskagent-agent-*.whl)"
        Push-Location (Join-Path $RepoRoot "runner")
        try {
            & uv sync --frozen --extra dev
            if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
            # Pre-package gate: any env-rot state in this checkout would
            # otherwise ride out inside the wheel (zero-byte
            # `typing_extensions.py` has shipped before). Run the whole
            # runner tests/ directory — narrower gates miss drift in
            # new tests that don't yet know they're "load-bearing".
            # Quiet by default: release gates pass 99% of the time so the
            # ~110-line -v report buries real signal. pytest re-runs -v
            # on its own when a test fails (per pytest's verbose-fallback
            # when -q hits a failure), so we don't add a manual re-run.
            & uv run --frozen --no-sync pytest tests/ -q
            if ($LASTEXITCODE -ne 0) {
                throw "runner test suite failed — see pytest output. Common cause: stale or corrupt transitive dep (typing_extensions, mcp, annotated_types) that would make the shipped wheel unstartable on user machines. Fix the env (try `uv cache clean` + `uv sync`) before retrying the build."
            }
            & uv build --wheel --out-dir dist
            if ($LASTEXITCODE -ne 0) { throw "uv build failed" }
        } finally { Pop-Location }
    } else {
        Write-Output "==> Skipping runner build (-SkipRunner)"
    }

    # 2. Build desktop.
    if (-not $SkipDesktop) {
        Write-Output "==> Building desktop (electron-builder → release\DeskAgent-${Version}-win-*-nsis.exe)"
        Push-Location (Join-Path $RepoRoot "client")
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
            & pnpm run $DesktopPnpmTarget
            if ($LASTEXITCODE -ne 0) { throw "pnpm run $DesktopPnpmTarget failed" }
        } finally { Pop-Location }
    } else {
        Write-Output "==> Skipping desktop build (-SkipDesktop)"
    }

    # 3. Locate desktop artifact.
    $desktopArtifact = Get-ChildItem -Path (Join-Path $RepoRoot "client\release") -Filter $DesktopArtifactGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $desktopArtifact) {
        throw "no desktop artifact matching '$DesktopArtifactGlob' in client\release\"
    }
    Write-Output "==> Desktop artifact: $($desktopArtifact.FullName)"

    # 4. Stage payload.
    Stage-Payload
    Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")


    # 6. Code-sign desktop (if a cert thumbprint is provided).
    if ($CertThumbprint) {
        Write-Output "==> Code-signing $($desktopArtifact.Name)"
        & $SignTool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a /sha1 $CertThumbprint $desktopArtifact.FullName
        if ($LASTEXITCODE -ne 0) { throw "signtool sign failed" }
        Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")
    }

    # 7. Patch tauri.conf.json, then Tauri build, then restore.
    # No NSIS wrap — ship DeskAgent-Setup.exe directly so the user double-clicks it
    # and immediately sees the install UI (single-file installer pattern,
    # avoids the double-installer problem where NSIS extracts DeskAgent-Setup.exe
    # to Program Files and the user then has to run it manually).
    Patch-TauriConfig
    try {
        Write-Output "==> Tauri build"
        Push-Location (Join-Path $RepoRoot "installer")
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
            & pnpm run tauri -- build --no-bundle
            if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
        } finally { Pop-Location }
    } finally {
        Restore-TauriConfig
    }

    # 8. Locate final installer — DeskAgent-Setup.exe at target/release (no NSIS wrapper).
    $finalDir = Join-Path $RepoRoot "installer\src-tauri\target\release"
    $finalGlob = "DeskAgent-Setup.exe"
    $final = Get-ChildItem -Path $finalDir -Filter $finalGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $final) {
        throw "Tauri build did not produce $finalDir\$finalGlob"
    }

    # 9. Copy to output dir with version-suffixed name.
    if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null }
    $finalName = "DeskAgent-Setup-${Version}.exe"
    Copy-Item -Force $final.FullName (Join-Path $OutputDir $finalName)
    Write-Output ""
    Write-Output "==> Final installer: $(Join-Path $OutputDir $finalName)"

    # 10. Build the self-update artifact. Same build, second zip — installed
    # clients fetch this from the backend to self-update desktop + runner
    # without re-running the outer Tauri installer.
    Build-UpdateZip `
        -Version $Version `
        -DesktopReleaseDir (Join-Path (Join-Path $RepoRoot 'desktop') 'release') `
        -RunnerWheelPath (Get-ChildItem (Join-Path $RepoRoot 'installer\payload\runner') -Filter 'deskagent-agent-*.whl' | Select-Object -First 1).FullName `
        -ServerPyPath (Join-Path $RepoRoot 'installer\payload\runner\server.py') `
        -OutputDir $OutputDir
} finally {
    Pop-Location
}
