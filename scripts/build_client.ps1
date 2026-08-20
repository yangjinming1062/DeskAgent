# 打包 SpiritAgent 客户端安装器（Windows）。Backend 由 Docker 单独构建，不在此入口内。
# 编排顺序：构建 runner wheel → electron-builder 桌面端 → 暂存 payload → 临时改 tauri.conf.json → Tauri 构建 → 还原配置 → 拷贝最终安装器。
# 用法：powershell -File scripts\build_client.ps1 -Version 0.16.0 [-SkipRunner] [-SkipDesktop] [-SignTool PATH] [-CertThumbprint TH] [-OutputDir DIR]

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

# 加载共享签名 + manifest 助手（Sign-Manifest / New-RunnerManifest / Resolve-UpdateSigningKey）。
. (Join-Path $ScriptDir 'lib/UpdateManifest.ps1')

if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot "release" }

$RunnerWheelGlob = "spiritagent-agent-*.whl"
$DesktopPnpmTarget = "dist:win:nsis"
$DesktopArtifactGlob = "SpiritAgent-${Version}-win-*.exe"
$DesktopFormat = "nsis"
$TauriBundleDir = "nsis"

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
    # server.py 一起打包到 payload，便于 install.sh/ps1 部署到 $SPIRITAGENT_HOME/runner/。
    Copy-Item -Force (Join-Path $RepoRoot "runner\server.py") (Join-Path $payloadRunner "server.py")

    # 把 skills 与 install.{sh,ps1} 链入 payload：目录走 junction（Windows 原生目录符号链接），文件走 hardlink；源都在 installer/ 子树内，相对解析简单。
    $skillsLink = Join-Path $RepoRoot "installer\payload\skills"
    $installShLink = Join-Path $RepoRoot "installer\payload\install.sh"
    $installPs1Link = Join-Path $RepoRoot "installer\payload\install.ps1"
    foreach ($p in @($skillsLink, $installShLink, $installPs1Link)) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }

    $cmdOutput = & cmd /c "mklink /J `"$skillsLink`" `"$RepoRoot\installer\skills`"" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "mklink skills failed: $cmdOutput" }
    # install.sh / install.ps1 是文件不是目录，改用 hardlink。
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
    # 构造自更新 zip：同时装入桌面端产物 + runner wheel + server.py，一次更新覆盖客户端两侧；
    # wheel 内已带 skills（runner/pyproject.toml 的 package_data），不需要单独的 skills tar。
    # 后端按 zip 内的 `runner/` 布局提取，便于后续一致性校验。
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

    $stageDir = Join-Path ([IO.Path]::GetTempPath()) "spiritagent-update-stage-$Version-$PID"
    if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
    New-Item -ItemType Directory -Path $stageDir | Out-Null

    try {
        # 仅拷贝当前版本产物与 manifest（剔除 win-unpacked/、构建调试文件、旧构建残留）。
        $versionPrefix = "SpiritAgent-$Version"
        Get-ChildItem -Path $DesktopReleaseDir -File | Where-Object {
            $_.Name -like "$versionPrefix*" -or
            $_.Name -match '^latest.*\.yml$' -or
            $_.Name -eq 'app-update.yml'
        } | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $stageDir -Force
        }

        # 暂存 runner wheel 与 server.py。
        $runnerStage = Join-Path $stageDir 'runner'
        New-Item -ItemType Directory -Path $runnerStage -Force | Out-Null
        Copy-Item -Force $RunnerWheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath))
        Copy-Item -Force $ServerPyPath (Join-Path $runnerStage 'server.py')

        # 一次性解析私钥（后续各平台重签与 New-RunnerManifest 共用）。
        $keyPath = Resolve-UpdateSigningKey

        # 兜底重签：Build-UpdateZip 是规范的签名点（见 lib/UpdateManifest.ps1::Sign-Manifest），此分支处理跨主机构建时 electron-builder 输出未签名 manifest 的情况。
        foreach ($name in @('latest.yml', 'latest-mac.yml')) {
            $manifestPath = Join-Path $stageDir $name
            if (-not (Test-Path $manifestPath)) { continue }
            $raw = Get-Content -Raw $manifestPath
            if ($raw -match '(?m)^\s*"?\s*signature\s*"?\s*[:=]') { continue }
            Write-Output "  re-signing $name (signature was missing)"
            Sign-Manifest -ManifestPath $manifestPath -KeyPath $keyPath
        }

        # 构造并签名 latest-runner.yml：桌面端主进程在重启前读取，先把 wheel + server.py 拉到本地、校验完成才提示用户 "Restart now"。
        New-RunnerManifest `
            -Version $Version `
            -WheelPath (Join-Path $runnerStage (Split-Path -Leaf $RunnerWheelPath)) `
            -ServerPyPath (Join-Path $runnerStage 'server.py') `
            -OutDir $stageDir `
            -KeyPath $keyPath | Out-Null

        # 选定规范的 Windows NSIS exe 写入 manifest.json，便于后端 _extract_archive_entries 无歧义定位。
        $desktopExe = Get-ChildItem $stageDir -Filter 'SpiritAgent-*-win-*.exe' -File | Select-Object -First 1
        $manifest = [ordered]@{
            version        = $Version
            desktop_path   = if ($desktopExe) { $desktopExe.Name } else { $null }
            runner_wheel   = "runner/$(Split-Path -Leaf $RunnerWheelPath)"
            server_py      = 'runner/server.py'
            manifests      = @('latest.yml', 'latest-mac.yml', 'latest-runner.yml')
        }
        ($manifest | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $stageDir 'manifest.json') -NoNewline

        # 打包 zip。
        $zipPath = Join-Path $OutputDir "SpiritAgent-${Version}-update.zip"
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

Push-Location $RepoRoot
try {
    Set-Version $Version

    if (-not $SkipRunner) {
        Write-Output "==> Building runner (uv build wheel → dist\spiritagent-agent-*.whl)"
        Push-Location (Join-Path $RepoRoot "runner")
        try {
            & uv sync --frozen --extra dev
            if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
            # 打包前的环境闸口：env-rot 状态（如 0 字节 typing_extensions.py）会一起钻进 wheel，跑整个 runner/tests/——更窄的闸门会漏掉尚不自知"承重"的新测试。
            # 默认静默：release 路径 99% 通过，-v 报告会淹没真实信号；pytest 在 -q 失败时自己回退到 -v，不必手动重跑。
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

    if (-not $SkipDesktop) {
        Write-Output "==> Building desktop (electron-builder → release\SpiritAgent-${Version}-win-*-nsis.exe)"
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

    $desktopArtifact = Get-ChildItem -Path (Join-Path $RepoRoot "client\release") -Filter $DesktopArtifactGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $desktopArtifact) {
        throw "no desktop artifact matching '$DesktopArtifactGlob' in client\release\"
    }
    Write-Output "==> Desktop artifact: $($desktopArtifact.FullName)"

    Stage-Payload
    Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")


    if ($CertThumbprint) {
        Write-Output "==> Code-signing $($desktopArtifact.Name)"
        & $SignTool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a /sha1 $CertThumbprint $desktopArtifact.FullName
        if ($LASTEXITCODE -ne 0) { throw "signtool sign failed" }
        Copy-Item -Force $desktopArtifact.FullName (Join-Path $RepoRoot "installer\payload\client\$($desktopArtifact.Name)")
    }

    # 不外层包 NSIS：直接产 SpiritAgent-Setup.exe，单文件安装器形态，双击即出 UI（避免 NSIS 把 .exe 解到 Program Files 后用户再手动跑一遍）。
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

    # 在 target/release 找最终 SpiritAgent-Setup.exe（不走 NSIS 包装）。
    $finalDir = Join-Path $RepoRoot "installer\src-tauri\target\release"
    $finalGlob = "SpiritAgent-Setup.exe"
    $final = Get-ChildItem -Path $finalDir -Filter $finalGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $final) {
        throw "Tauri build did not produce $finalDir\$finalGlob"
    }

    if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null }
    $finalName = "SpiritAgent-Setup-${Version}.exe"
    Copy-Item -Force $final.FullName (Join-Path $OutputDir $finalName)
    Write-Output ""
    Write-Output "==> Final installer: $(Join-Path $OutputDir $finalName)"

    # 同一构建再产一个自更新 zip：已安装客户端从后端拉它自更桌面端 + runner，无需重跑 Tauri 安装器。
    Build-UpdateZip `
        -Version $Version `
        -DesktopReleaseDir (Join-Path (Join-Path $RepoRoot 'desktop') 'release') `
        -RunnerWheelPath (Get-ChildItem (Join-Path $RepoRoot 'installer\payload\runner') -Filter 'spiritagent-agent-*.whl' | Select-Object -First 1).FullName `
        -ServerPyPath (Join-Path $RepoRoot 'installer\payload\runner\server.py') `
        -OutputDir $OutputDir
} finally {
    Pop-Location
}
