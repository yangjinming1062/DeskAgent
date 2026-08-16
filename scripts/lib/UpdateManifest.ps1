# scripts/lib/UpdateManifest.ps1
# Shared signing + manifest helpers for the SpiritAgent update pipeline.
#
# Imported by:
#   - scripts/build_client.ps1 (Build-UpdateZip)
#
# Pure functions over the filesystem; no module-level state.

# Resolve the full path to openssl.exe.
# Git for Windows ships openssl in its mingw64/bin dir, which may not be on
# PATH when the script is invoked from a non-git shell.
function Resolve-OpenSsl {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    # Fallback: look in common Git install locations.
    $candidates = @(
        Join-Path $env:ProgramFiles 'Git\mingw64\bin\openssl.exe'
        Join-Path ${env:ProgramFiles(x86)} 'Git\mingw64\bin\openssl.exe'
        Join-Path $env:LOCALAPPDATA 'Programs\Git\mingw64\bin\openssl.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }

    throw "openssl not found. Install Git for Windows (includes openssl) or add openssl to PATH."
}

# Resolve the PEM private key used to sign update manifests.
#
# Returns the absolute path to scripts/secrets/update.key in the repo.
# Throws if the file is missing (it is committed to the repo and should
# always be present after a normal clone).
function Resolve-UpdateSigningKey {
    [CmdletBinding()]
    param()

    $scriptDir = Split-Path -Parent $PSCommandPath
    $repoRoot = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
    $keyPath = Join-Path $repoRoot 'scripts\secrets\update.key'

    if (-not (Test-Path $keyPath)) {
        throw "Update signing key not found at scripts\secrets\update.key. The key is committed to the repo — ensure the file exists."
    }

    return $keyPath
}

# Sign a Squirrel-style manifest in place.
#
# Reads the JSON manifest at $ManifestPath, computes the SHA-512 of the file
# referenced by its top-level `path` field, normalizes to UPPERCASE hex, and
# writes back a `signature` field produced by `openssl dgst -sha512 -sign`
# over the payload "<path>|<sha512>". Also rewrites `sha512` and `files[]`
# to reflect the current on-disk file.
#
# Throws if the manifest has no `path` field, if the referenced file is
# missing, or if openssl is not in PATH.
function Sign-Manifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$KeyPath,
        # Pre-computed digest of the binary the manifest points at. Pass this
        # when the caller already has the SHA-512/size in hand (e.g. when
        # building a fresh manifest) so we avoid re-hashing a multi-hundred-MB
        # wheel twice.
        [string]$Sha512 = '',
        [long]$Size = -1
    )

    if (-not (Test-Path $ManifestPath)) {
        Write-Warning "Manifest $ManifestPath does not exist; skipping"
        return
    }

    $manifestDir = Split-Path -Parent $ManifestPath
    $raw = Get-Content $ManifestPath -Raw

    # Extract the `path` value with regex so we handle both JSON and YAML
    # manifests without requiring a YAML parser.
    $pathMatch = [regex]::Match($raw, '(?m)^\s*"?\s*path\s*"?\s*[:=]\s*"?\s*(.+?)\s*"?\s*,?\s*$')
    if (-not $pathMatch.Success) {
        Write-Warning "Manifest $ManifestPath has no 'path' field; skipping"
        return
    }
    $pathValue = $pathMatch.Groups[1].Value.Trim('"', "'", ',')

    $binaryPath = Join-Path $manifestDir $pathValue
    if (-not (Test-Path $binaryPath)) {
        throw "Manifest $ManifestPath references missing file: $binaryPath"
    }

    if ($Sha512 -and $Size -ge 0) {
        $sha512 = $Sha512.ToUpper()
        $size = $Size
    } else {
        $sha512 = (Get-FileHash $binaryPath -Algorithm SHA512).Hash.ToUpper()
        $size = (Get-Item $binaryPath).Length
    }

    # Compute the RSA signature. Write to temp files because PowerShell's
    # & operator captures binary stdout as an array, not a byte stream.
    $openssl = Resolve-OpenSsl
    $payload = "$pathValue|$sha512"
    $tmpPayload = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-sign-payload-$PID.tmp"
    $tmpSig = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-sign-sig-$PID.tmp"
    try {
        [IO.File]::WriteAllBytes($tmpPayload, [Text.Encoding]::UTF8.GetBytes($payload))
        & $openssl dgst -sha512 -sign $KeyPath -out $tmpSig $tmpPayload 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "openssl sign failed for $ManifestPath (exit $LASTEXITCODE)"
        }
        $signatureBytes = [IO.File]::ReadAllBytes($tmpSig)
    } finally {
        Remove-Item $tmpPayload, $tmpSig -ErrorAction SilentlyContinue
    }
    $signatureB64 = [Convert]::ToBase64String($signatureBytes)

    # Update fields via regex so we handle both YAML and JSON manifests
    # without requiring a YAML parser.
    $updated = $raw

    # sha512:  sha512: <value>  or  "sha512": "<value>"
    $updated = $updated -replace '(?m)(\s*"?\s*sha512\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$sha512"

    # files array: replace the entire block with a single-entry array.
    # YAML:   files:\n  - url: ...\n    sha512: ...\n    size: ...
    # JSON:   "files": [{"url": "...", "sha512": "...", "size": ...}]
    $filesBlockYaml = "  - url: $pathValue`n    sha512: $sha512`n    size: $size"
    $filesBlockJson = "`"files`": [{`"url`": `"$pathValue`", `"sha512`": `"$sha512`", `"size`": $size}]"
    if ($updated -match '(?m)^\s*"?\s*files\s*"?\s*:\s*\[') {
        # JSON-style files array
        $updated = $updated -replace '(?m)(\s*"?\s*files\s*"?\s*:\s*)\[.*?\]', "`${1}$filesBlockJson"
    } else {
        # YAML-style files block: match from "files:" through the indented list
        # items until the next top-level key or end of string.
        $updated = $updated -replace '(?s)(files:\s*\n)(\s+-[\s\S]*?)(?=\n\S|\n\n|\z)', "`${1}$filesBlockYaml"
    }

    # signature: add or replace
    if ($updated -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') {
        $updated = $updated -replace '(?m)(\s*"?\s*signature\s*"?\s*[:=]\s*"?\s*)[^\r\n"]*', "`${1}$signatureB64"
    } else {
        $updated = $updated.TrimEnd() + "`nsignature: $signatureB64`n"
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ManifestPath, $updated, $utf8NoBom)
}

# Build a signed `latest-runner.yml` manifest for the runner wheel + server.py.
#
# The output is a Squirrel-compatible YAML where:
#   - top-level `path` / `sha512` / `files[]` point at the wheel (electron-updater
#     ignores this field, but the file must still validate as a Squirrel manifest)
#   - `runner` block carries wheel_filename / wheel_sha512 / wheel_size /
#     server_py_sha256
#   - `signature` is RSA-signed over "<path>|<sha512>" using the same keypair
#     as the desktop binary
#
# Returns the absolute path to the signed YAML.
function New-RunnerManifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$WheelPath,
        [Parameter(Mandatory)][string]$ServerPyPath,
        [Parameter(Mandatory)][string]$OutDir,
        [Parameter(Mandatory)][string]$KeyPath
    )

    if (-not (Test-Path $WheelPath)) {
        throw "Wheel not found: $WheelPath"
    }
    if (-not (Test-Path $ServerPyPath)) {
        throw "server.py not found: $ServerPyPath"
    }

    $wheelName = Split-Path -Leaf $WheelPath
    $serverPyName = Split-Path -Leaf $ServerPyPath

    # The Squirrel convention is for `path` to be relative to the manifest's
    # directory. We mirror the build layout: `runner/spiritagent-agent-*.whl` is
    # staged under `<staging>/runner/`, so the manifest is written to the
    # staging root and `path` is `runner/<wheel>`.
    $wheelRel = "runner/$wheelName"
    $serverPyRel = "runner/$serverPyName"

    $stagedWheel = Join-Path $OutDir $wheelRel
    $stagedServer = Join-Path $OutDir $serverPyRel
    New-Item -ItemType Directory -Path (Split-Path $stagedWheel -Parent) -Force | Out-Null
    if (-not (Test-Path $stagedWheel)) {
        Copy-Item $WheelPath $stagedWheel -Force
    }
    if (-not (Test-Path $stagedServer)) {
        Copy-Item $ServerPyPath $stagedServer -Force
    }

    $wheelSha = (Get-FileHash $stagedWheel -Algorithm SHA512).Hash.ToUpper()
    $wheelSize = (Get-Item $stagedWheel).Length
    $serverPySha256 = (Get-FileHash $stagedServer -Algorithm SHA256).Hash.ToLower()

    $manifest = [ordered]@{
        version      = $Version
        path         = $wheelRel
        sha512       = $wheelSha
        size         = $wheelSize
        files        = @(@{ url = $wheelRel; sha512 = $wheelSha; size = $wheelSize })
        runner       = [ordered]@{
            version          = $Version
            wheel_filename   = $wheelRel
            wheel_sha512     = $wheelSha
            wheel_size       = $wheelSize
            server_py_sha256 = $serverPySha256
        }
    }

    $manifestPath = Join-Path $OutDir 'latest-runner.yml'
    ($manifest | ConvertTo-Json -Depth 8) | Set-Content -Path $manifestPath -NoNewline

    # Pass the SHA-512 + size we already computed (the wheel was just hashed
    # and stat'd on lines above). Sign-Manifest falls back to a re-hash when
    # these aren't passed, which would mean reading the multi-hundred-MB wheel
    # twice per build.
    Sign-Manifest -ManifestPath $manifestPath -KeyPath $KeyPath -Sha512 $wheelSha -Size $wheelSize

    return $manifestPath
}
