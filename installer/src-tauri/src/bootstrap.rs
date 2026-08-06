//! Bootstrap orchestration.
//!
//! Direct port of `runBootstrap` from `desktop/main/lifecycle/platform.cjs`.
//! Drives install.ps1 / install.sh stage-by-stage, emits progress events
//! over the Tauri `bootstrap` channel, writes a forensic log to
//! DESKAGENT_HOME/logs/bootstrap-<timestamp>.log.
//!
//! Lifecycle:
//!   1. `start_bootstrap` (Tauri command) → spawns the worker task.
//!   2. Worker resolves install script (dev/cache/download).
//!   3. Worker calls `install.ps1 -Manifest` → emits `manifest` event.
//!   4. Worker iterates stages, calling `install.ps1 -Stage NAME -NonInteractive -Json`.
//!   5. On success → `complete`. On any stage failure → `failed`. On cancel → `failed`.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::{mpsc, Mutex};

use crate::events::{BootstrapEvent, LogStream, Manifest, StageState};
use crate::install_script::{self, ScriptKind, ScriptSource};
use crate::powershell::{self, BundleContext, StreamSink};
use crate::AppState;

// ---------------------------------------------------------------------------
// Public Tauri commands
// ---------------------------------------------------------------------------

/// Frontend → Rust: kick off the install.
#[derive(Debug, Deserialize)]
pub struct StartBootstrapArgs {
    /// Optional override for the commit pin. Defaults to the build-time
    /// pin baked in via `BUILD_PIN_COMMIT`.
    pub commit: Option<String>,
    /// Optional override for the branch pin. Defaults to `BUILD_PIN_BRANCH`.
    pub branch: Option<String>,
    /// Reserved for the legacy `Stage-Desktop` flow that built apps/desktop
    /// inside the install script. The slim 5-stage install.{sh,ps1} no
    /// longer has a desktop stage (desktop is prebuilt and shipped as a
    /// Tauri-bundled artifact), so this flag is now dead — kept on the wire
    /// so the frontend can still pass it without 400s. Defaults to false.
    #[serde(default)]
    pub include_desktop: bool,
    /// Optional override for DESKAGENT_HOME. Tests use this; production
    /// almost always falls back to the OS default.
    pub deskagent_home: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct BootstrapStatus {
    pub running: bool,
    pub completed: bool,
    pub install_root: Option<String>,
    pub last_error: Option<String>,
}

/// Handle stored in AppState while a bootstrap run is in flight. Carries
/// the cancellation channel and the most recent terminal status so the
/// frontend can re-query after a window refresh.
pub struct BootstrapHandle {
    pub cancel_tx: mpsc::Sender<()>,
    pub started_at: Instant,
    pub status: BootstrapStatus,
}

#[tauri::command]
pub async fn start_bootstrap(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
    args: StartBootstrapArgs,
) -> Result<(), String> {
    let mut guard = state.bootstrap.lock().await;
    if let Some(h) = guard.as_ref() {
        if h.status.running {
            return Err("Bootstrap is already running".into());
        }
    }

    let (cancel_tx, cancel_rx) = mpsc::channel::<()>(1);
    let handle = BootstrapHandle {
        cancel_tx,
        started_at: Instant::now(),
        status: BootstrapStatus {
            running: true,
            completed: false,
            install_root: None,
            last_error: None,
        },
    };
    *guard = Some(handle);
    drop(guard);

    let app_for_task = app.clone();
    let state_for_task = state.inner().clone();
    let args_for_task = args;
    let cancel_rx = Arc::new(Mutex::new(Some(cancel_rx)));

    tokio::spawn(async move {
        let result = run_bootstrap(app_for_task.clone(), args_for_task, cancel_rx).await;

        // Reflect terminal state into AppState so get_bootstrap_status()
        // can serve it after the task exits.
        let mut guard = state_for_task.bootstrap.lock().await;
        if let Some(h) = guard.as_mut() {
            h.status.running = false;
            match &result {
                Ok(install_root) => {
                    h.status.completed = true;
                    h.status.install_root = Some(install_root.clone());
                    h.status.last_error = None;
                }
                Err(err) => {
                    h.status.completed = false;
                    h.status.last_error = Some(err.to_string());
                }
            }
        }
    });

    Ok(())
}

#[tauri::command]
pub async fn cancel_bootstrap(state: State<'_, Arc<AppState>>) -> Result<(), String> {
    let guard = state.bootstrap.lock().await;
    if let Some(h) = guard.as_ref() {
        let _ = h.cancel_tx.try_send(());
    }
    Ok(())
}

#[tauri::command]
pub async fn get_bootstrap_status(
    state: State<'_, Arc<AppState>>,
) -> Result<BootstrapStatus, String> {
    let guard = state.bootstrap.lock().await;
    Ok(match guard.as_ref() {
        Some(h) => BootstrapStatus {
            running: h.status.running,
            completed: h.status.completed,
            install_root: h.status.install_root.clone(),
            last_error: h.status.last_error.clone(),
        },
        None => BootstrapStatus {
            running: false,
            completed: false,
            install_root: None,
            last_error: None,
        },
    })
}

/// Spawn the locally-built DeskAgent desktop binary, then close the installer
/// window. The desktop path is resolved from the platform's standard install
/// location (set by Stage-UnpackDesktop of install.{sh,ps1}).
///
/// Returns Err with a human-readable message if the binary doesn't exist
/// (e.g. when Stage-UnpackDesktop was skipped) so the frontend can present
/// actionable failure UI rather than silently doing nothing.
#[tauri::command]
pub async fn launch_deskagent_desktop(app: AppHandle) -> Result<(), String> {
    let exe_path = resolve_deskagent_desktop_exe().ok_or_else(|| {
        format!(
            "在预期的平台位置 ({}) 未找到已安装的 DeskAgent 桌面应用。请重新运行 DeskAgent-Setup 以安装桌面组件。",
            desktop_install_root().display()
        )
    })?;

    tracing::info!(?exe_path, "launching DeskAgent desktop");

    // Detach from us — the installer is about to exit. On macOS launch the
    // bundle through LaunchServices instead of exec'ing Contents/MacOS/DeskAgent
    // directly; this matches user double-click/open behavior and avoids cwd /
    // quarantine oddities after a self-update rebuild.
    let mut cmd = desktop_launch_command(&exe_path);
    #[cfg(target_os = "windows")]
    {
        // DETACHED_PROCESS = 0x00000008
        cmd.creation_flags(0x0000_0008);
    }

    cmd.spawn().map_err(|e| {
        format!("failed to launch {}: {e}", exe_path.display())
    })?;

    // Give Windows ~150ms to actually start the new process before we exit.
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;

    // Exit the installer cleanly. Tauri's process plugin gives us the
    // right hook regardless of platform.
    app.exit(0);
    Ok(())
}

/// Test-only override for `desktop_install_root()`. Production paths are
/// platform-canonical (`/Applications/DeskAgent.app` etc); tests need to redirect
/// to a tmp dir because the production paths aren't writable in CI.
#[cfg(test)]
static DESKTOP_ROOT_OVERRIDE: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

#[cfg(test)]
pub(crate) fn set_desktop_root_override_for_test(p: PathBuf) {
    let _ = DESKTOP_ROOT_OVERRIDE.set(p);
}

/// The platform-canonical directory DeskAgent desktop installs to. Mirrors
/// install.{sh,ps1} Stage-UnpackDesktop.
pub(crate) fn desktop_install_root() -> PathBuf {
    #[cfg(test)]
    {
        if let Some(p) = DESKTOP_ROOT_OVERRIDE.get() {
            return p.clone();
        }
    }
    #[cfg(target_os = "macos")]
    {
        PathBuf::from("/Applications/DeskAgent.app")
    }
    #[cfg(target_os = "windows")]
    {
        // %LOCALAPPDATA%\Programs\DeskAgent — matches the NSIS /D= path the
        // slim install.ps1 uses in Stage-UnpackDesktop.
        dirs::data_local_dir()
            .map(|p| p.join("Programs").join("DeskAgent"))
            .unwrap_or_else(|| PathBuf::from("C:/Program Files/DeskAgent"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        // Unreachable: installer only ships for macOS / Windows. An empty
        // PathBuf keeps the function total without pretending any real
        // path exists on an unsupported host.
        PathBuf::new()
    }
}

/// Resolves the installed desktop binary at its platform-canonical path.
/// Returns the .app bundle on macOS, the .exe on Windows.
pub(crate) fn resolve_deskagent_desktop_exe() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        let exe = desktop_install_root().join("Contents").join("MacOS").join("DeskAgent");
        if exe.exists() {
            return Some(exe);
        }
    }
    #[cfg(target_os = "windows")]
    {
        let exe = desktop_install_root().join("DeskAgent.exe");
        if exe.exists() {
            return Some(exe);
        }
    }
    None
}

#[allow(dead_code)]
pub(crate) fn resolve_deskagent_desktop_app() -> Option<PathBuf> {
    let exe = resolve_deskagent_desktop_exe()?;
    #[cfg(target_os = "macos")]
    {
        // .../DeskAgent.app/Contents/MacOS/DeskAgent -> .../DeskAgent.app
        let app = exe.parent()?.parent()?.parent()?.to_path_buf();
        if app.extension().and_then(|e| e.to_str()) == Some("app") && app.is_dir() {
            return Some(app);
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        return Some(exe);
    }
    #[allow(unreachable_code)]
    None
}

/// Gates `deskagent_is_installed` so a broken venv can never satisfy the
/// macOS launcher fast-path. The import chain must match
/// `desktop/main/runner-updater.cjs::_probeVenvIntegrity` so the
/// two gates never disagree on what "venv is healthy" means.
fn runner_venv_is_healthy() -> bool {
    use std::process::{Command, Stdio};

    let Some(venv_python) = crate::paths::runner_venv_python() else {
        return false;
    };

    Command::new(&venv_python)
        .arg("-c")
        .arg(
            "from typing_extensions import Sentinel; from annotated_types import BaseMetadata; from mcp.types import BaseModel",
        )
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// True when a prior install completed (bootstrap-complete marker present) AND a
/// launchable desktop app exists on disk AND the Runner venv is intact
/// (`runner_venv_is_healthy`). Used by the installer's launcher fast path
/// so a bare re-open just opens DeskAgent instead of re-running setup — and
/// conversely, so a stale marker over a broken venv can never silently
/// skip the install protocol.
pub(crate) fn deskagent_is_installed() -> bool {
    crate::paths::deskagent_home()
        .join(".deskagent-bootstrap-complete")
        .exists()
        && resolve_deskagent_desktop_exe().is_some()
        && runner_venv_is_healthy()
}

/// Spawn the already-built desktop app, detached. Returns Err if no built app
/// exists or the spawn fails, so the caller can fall back to showing the
/// installer UI.
pub(crate) fn spawn_installed_desktop() -> std::io::Result<()> {
    let exe = resolve_deskagent_desktop_exe().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "no installed DeskAgent desktop app")
    })?;
    let mut cmd = desktop_launch_command_std(&exe);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // DETACHED_PROCESS = 0x00000008 — keep the desktop alive after the
        // installer exits, mirroring launch_deskagent_desktop. Kept correct here
        // even though the only caller is macOS-gated today, so future reuse on
        // Windows doesn't reintroduce the relaunch race.
        cmd.creation_flags(0x0000_0008);
    }
    cmd.spawn().map(|_child| ())
}

#[cfg(target_os = "macos")]
pub(crate) fn open_macos_app_detached(app_bundle: &std::path::Path) -> std::io::Result<()> {
    let mut cmd = std::process::Command::new("/usr/bin/open");
    cmd.arg(app_bundle);
    cmd.current_dir(crate::paths::deskagent_home());
    cmd.spawn().map(|_child| ())
}

#[cfg(target_os = "macos")]
fn app_bundle_for_exe(exe: &std::path::Path) -> Option<PathBuf> {
    let app = exe.parent()?.parent()?.parent()?.to_path_buf();
    if app.extension().and_then(|e| e.to_str()) == Some("app") && app.is_dir() {
        Some(app)
    } else {
        None
    }
}

fn desktop_launch_command(exe_path: &std::path::Path) -> tokio::process::Command {
    #[cfg(target_os = "macos")]
    {
        if let Some(app_bundle) = app_bundle_for_exe(exe_path) {
            let mut cmd = tokio::process::Command::new("/usr/bin/open");
            cmd.arg(app_bundle);
            cmd.current_dir(crate::paths::deskagent_home());
            return cmd;
        }
    }

    let mut cmd = tokio::process::Command::new(exe_path);
    cmd.current_dir(exe_path.parent().unwrap_or_else(|| Path::new(".")));
    cmd
}

fn desktop_launch_command_std(exe_path: &std::path::Path) -> std::process::Command {
    #[cfg(target_os = "macos")]
    {
        if let Some(app_bundle) = app_bundle_for_exe(exe_path) {
            let mut cmd = std::process::Command::new("/usr/bin/open");
            cmd.arg(app_bundle);
            cmd.current_dir(crate::paths::deskagent_home());
            return cmd;
        }
    }

    let mut cmd = std::process::Command::new(exe_path);
    cmd.current_dir(exe_path.parent().unwrap_or_else(|| Path::new(".")));
    cmd
}

// ---------------------------------------------------------------------------
// Bootstrap implementation
// ---------------------------------------------------------------------------

async fn run_bootstrap(
    app: AppHandle,
    args: StartBootstrapArgs,
    cancel_rx_holder: Arc<Mutex<Option<mpsc::Receiver<()>>>>,
) -> Result<String> {
    let kind = ScriptKind::for_current_os();

    // Pin metadata for the install marker event. The install script itself
    // is bundled (no commit/branch pin needed for resolution), but the
    // marker records which version of the agent code the user pinned to.
    let pinned_commit = args
        .commit
        .clone()
        .or_else(|| option_env!("BUILD_PIN_COMMIT").map(|s| s.to_string()));
    let pinned_branch = args
        .branch
        .clone()
        .or_else(|| option_env!("BUILD_PIN_BRANCH").map(|s| s.to_string()));

    tracing::info!(
        pinned_commit = ?pinned_commit,
        pinned_branch = ?pinned_branch,
        kind = ?kind,
        include_desktop = args.include_desktop,
        "bootstrap starting"
    );

    let app_for_log = app.clone();
    let emit_log = move |line: &str| {
        emit_event(
            &app_for_log,
            BootstrapEvent::Log {
                stage: None,
                line: line.to_string(),
                stream: LogStream::Stdout,
            },
        );
        // Bump to info-level so the line shows in bootstrap-installer.log
        // under the default INFO filter. Previously this was debug! which
        // got dropped on the floor, leaving us blind whenever install.ps1
        // failed — the log only had the "bootstrap starting" banner.
        tracing::info!(target: "bootstrap.log", "{line}");
    };

    // 1. Resolve install.{ps1,sh} — either from $DESKAGENT_SETUP_DEV_REPO_ROOT
    // (dev shortcut) or from the Tauri bundle.resources (production). The
    // installer binary is self-contained; no network fallback.
    let script = install_script::resolve(&app, kind, &emit_log)
        .await
        .map_err(|e| {
            let msg = format!("resolve install script failed: {e:#}");
            emit_event(
                &app,
                BootstrapEvent::Failed {
                    stage: None,
                    error: msg.clone(),
                },
            );
            anyhow!(msg)
        })?;

    let source_note = match &script.source {
        ScriptSource::DevCheckout => "dev checkout",
        ScriptSource::Bundled => "bundled",
    };
    emit_log(&format!(
        "[bootstrap] script {} via {}",
        script.path.display(),
        source_note
    ));

    // 2. Fetch manifest
    //
    // The slim 5-stage install.{sh,ps1} no longer takes -IncludeDesktop
    // (desktop is prebuilt and embedded as a Tauri bundle.resource, not
    // built by the install script). The flag is kept in StartBootstrapArgs
    // for wire compatibility but is never forwarded to the script.
    // The install script no longer takes -Commit / -Branch either — the
    // script is bundled, so commit/branch are only used for the marker
    // event below.
    let manifest_args = vec!["-Manifest".to_string()];

    // Build the bundle context so the install script can find its payload
    // (runner / desktop / skills / config) under Tauri bundle.resources.
    let bundle_ctx = build_bundle_context(&app);

    let manifest_result = run_install_script(
        &app,
        &script.path,
        &manifest_args,
        args.deskagent_home.as_deref(),
        &bundle_ctx,
        None,
        Some("__manifest__".to_string()),
    )
    .await?;

    if manifest_result.exit_code != Some(0) {
        let err = format!(
            "install.ps1 -Manifest failed: exit {:?}\n{}",
            manifest_result.exit_code,
            manifest_result.stderr.trim()
        );
        emit_event(
            &app,
            BootstrapEvent::Failed {
                stage: None,
                error: err.clone(),
            },
        );
        return Err(anyhow!(err));
    }

    let manifest: Manifest = powershell::parse_manifest(&manifest_result.stdout).ok_or_else(|| {
        let err = format!(
            "install.ps1 -Manifest produced no parseable JSON payload\n{}",
            truncate(&manifest_result.stdout, MANIFEST_PREVIEW_CHARS)
        );
        emit_event(
            &app,
            BootstrapEvent::Failed {
                stage: None,
                error: err.clone(),
            },
        );
        anyhow!(err)
    })?;

    emit_event(
        &app,
        BootstrapEvent::Manifest {
            stages: manifest.stages.clone(),
            protocol_version: manifest.protocol_version,
        },
    );

    // 3. Iterate stages.
    for stage in &manifest.stages {
        if cancellation_signalled(&cancel_rx_holder).await {
            let err = "bootstrap cancelled by user".to_string();
            emit_event(
                &app,
                BootstrapEvent::Failed {
                    stage: Some(stage.name.clone()),
                    error: err.clone(),
                },
            );
            return Err(anyhow!(err));
        }

        let started = Instant::now();
        emit_event(
            &app,
            BootstrapEvent::Stage {
                name: stage.name.clone(),
                state: StageState::Running,
                duration_ms: None,
                result: None,
                error: None,
            },
        );

        let stage_args = vec![
            "-Stage".to_string(),
            stage.name.clone(),
            "-NonInteractive".to_string(),
            "-Json".to_string(),
        ];

        // Each stage gets its own cancel receiver because tokio::select!
        // in run_script consumes it. Take/return through the Arc<Mutex>.
        let local_cancel_rx = cancel_rx_holder.lock().await.take();

        let stage_result = run_install_script(
            &app,
            &script.path,
            &stage_args,
            args.deskagent_home.as_deref(),
            &bundle_ctx,
            local_cancel_rx,
            Some(stage.name.clone()),
        )
        .await?;

        let duration_ms = started.elapsed().as_millis() as u64;

        if stage_result.killed {
            emit_event(
                &app,
                BootstrapEvent::Stage {
                    name: stage.name.clone(),
                    state: StageState::Failed,
                    duration_ms: Some(duration_ms),
                    result: None,
                    error: Some("cancelled by user".into()),
                },
            );
            emit_event(
                &app,
                BootstrapEvent::Failed {
                    stage: Some(stage.name.clone()),
                    error: "cancelled by user".into(),
                },
            );
            return Err(anyhow!("cancelled by user"));
        }

        let result_frame = powershell::parse_stage_result(&stage_result.stdout);

        match result_frame {
            None => {
                let stdout_preview = truncate(&stage_result.stdout, STAGE_PREVIEW_CHARS);
                let stderr_preview = truncate(&stage_result.stderr, STAGE_PREVIEW_CHARS);
                tracing::error!(
                    stage = %stage.name,
                    exit = ?stage_result.exit_code,
                    stdout_len = stage_result.stdout.len(),
                    stderr_len = stage_result.stderr.len(),
                    stdout = %stdout_preview,
                    stderr = %stderr_preview,
                    "stage produced no JSON result frame"
                );
                let err = format!(
                    "install.ps1 -Stage {} produced no JSON result frame (exit={:?})\nstdout: {}\nstderr: {}",
                    stage.name, stage_result.exit_code, stdout_preview, stderr_preview
                );
                emit_event(
                    &app,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Failed,
                        duration_ms: Some(duration_ms),
                        result: None,
                        error: Some(err.clone()),
                    },
                );
                emit_event(
                    &app,
                    BootstrapEvent::Failed {
                        stage: Some(stage.name.clone()),
                        error: err.clone(),
                    },
                );
                return Err(anyhow!(err));
            }
            Some(frame) if frame.ok && frame.skipped => {
                emit_event(
                    &app,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Skipped,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: None,
                    },
                );
            }
            Some(frame) if frame.ok => {
                emit_event(
                    &app,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Succeeded,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: None,
                    },
                );
            }
            Some(frame) => {
                let err = frame
                    .reason
                    .clone()
                    .unwrap_or_else(|| format!("exit code {:?}", stage_result.exit_code));
                emit_event(
                    &app,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Failed,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: Some(err.clone()),
                    },
                );
                emit_event(
                    &app,
                    BootstrapEvent::Failed {
                        stage: Some(stage.name.clone()),
                        error: err.clone(),
                    },
                );
                return Err(anyhow!(err));
            }
        }
    }

    // 4. Resolve install_root. The slim 5-stage install.{sh,ps1} no longer
    // clones the repo into a `<deskagent_home>/deskagent-agent/` subdir — payload goes
    // straight into $DESKAGENT_HOME (bin/, skills/, config.yaml, .deskagent-bootstrap-
    // complete). So install_root IS deskagent_home.
    let deskagent_home = args
        .deskagent_home
        .clone()
        .unwrap_or_else(|| crate::paths::deskagent_home().to_string_lossy().into_owned());
    let install_root = PathBuf::from(&deskagent_home);

    // Copy ourselves to DESKAGENT_HOME/deskagent-setup.exe so start-menu / desktop
    // shortcuts have a stable target. This is a one-shot install concern;
    // a prior copy is detected and the self-copy is skipped. Best-effort —
    // a failure here must not fail an otherwise-successful install.
    if let Err(err) = crate::paths::copy_self_to_deskagent_home() {
        tracing::warn!(?err, "failed to copy installer into DESKAGENT_HOME (non-fatal)");
        emit_log(&format!(
            "[bootstrap] warning: could not stage installer binary: {err}"
        ));
    }

    emit_event(
        &app,
        BootstrapEvent::Complete {
            install_root: install_root.to_string_lossy().into_owned(),
            marker: Some(serde_json::json!({
                "pinnedCommit": pinned_commit,
                "pinnedBranch": pinned_branch,
            })),
        },
    );

    Ok(install_root.to_string_lossy().into_owned())
}

async fn cancellation_signalled(holder: &Arc<Mutex<Option<mpsc::Receiver<()>>>>) -> bool {
    let mut guard = holder.lock().await;
    if let Some(rx) = guard.as_mut() {
        rx.try_recv().is_ok()
    } else {
        false
    }
}

async fn run_install_script(
    app: &AppHandle,
    script_path: &std::path::Path,
    args: &[String],
    deskagent_home_override: Option<&str>,
    bundle: &BundleContext,
    cancel_rx: Option<mpsc::Receiver<()>>,
    stage_name: Option<String>,
) -> Result<powershell::ScriptResult> {
    let app_for_stdout = app.clone();
    let stage_for_stdout = stage_name.clone();
    let app_for_stderr = app.clone();
    let stage_for_stderr = stage_name.clone();
    let stage_for_stdout_log = stage_name.clone();
    let stage_for_stderr_log = stage_name.clone();

    let sink = StreamSink {
        on_stdout_line: Box::new(move |line: &str| {
            emit_event(
                &app_for_stdout,
                BootstrapEvent::Log {
                    stage: stage_for_stdout.clone(),
                    line: line.to_string(),
                    stream: LogStream::Stdout,
                },
            );
            // Tee to the rolling installer log so we have a persistent
            // record of every install.ps1 line. Without this, the only
            // log evidence of a failure was the Tauri event stream —
            // which gets discarded the moment the failure route mounts.
            match &stage_for_stdout_log {
                Some(name) => {
                    tracing::info!(target: "bootstrap.log", stage = %name, "{line}")
                }
                None => tracing::info!(target: "bootstrap.log", "{line}"),
            }
        }),
        on_stderr_line: Box::new(move |line: &str| {
            emit_event(
                &app_for_stderr,
                BootstrapEvent::Log {
                    stage: stage_for_stderr.clone(),
                    line: line.to_string(),
                    stream: LogStream::Stderr,
                },
            );
            // stderr-level lines get warn! so they're visually distinct
            // when scrolling through the log later.
            match &stage_for_stderr_log {
                Some(name) => {
                    tracing::warn!(target: "bootstrap.log", stage = %name, "stderr: {line}")
                }
                None => tracing::warn!(target: "bootstrap.log", "stderr: {line}"),
            }
        }),
    };

    powershell::run_script(script_path, args, sink, deskagent_home_override, bundle, cancel_rx)
        .await
        .map_err(|e| {
            tracing::error!(?e, "install script invocation failed");
            anyhow!("install script invocation failed: {e:#}")
        })
}

/// Builds a `BundleContext` from the running installer's Tauri resource dir.
/// Paths are relative to `<bundle.resources>/payload/` (see
/// `tauri.conf.json#bundle.resources`).
fn build_bundle_context(app: &AppHandle) -> BundleContext {
    let bundle_dir = app.path().resource_dir().ok();
    let payload = bundle_dir.as_ref().map(|d| d.join("payload"));

    let effective_payload = if payload.as_ref().is_some_and(|p| p.join("runner").is_dir()) {
        payload
    } else {
        Some(crate::embedded_resources::extract_resources().to_path_buf())
    };

    let installer_format = if cfg!(target_os = "macos") {
        "dmg"
    } else {
        "nsis"
    }
    .to_string();

    BundleContext {
        bundle_dir,
        bundled_runner_dir: effective_payload.as_ref().map(|d| d.join("runner")),
        bundled_desktop_dir: effective_payload.as_ref().map(|d| d.join("desktop")),
        bundled_skills_dir: effective_payload.as_ref().map(|d| d.join("skills")),
        bundled_voices_dir: effective_payload.as_ref().map(|d| d.join("voices")),
        bundled_onboarding_audio_dir: effective_payload.as_ref().map(|d| d.join("onboarding-audio")),
        config_path: effective_payload.as_ref().map(|d| d.join("config.yaml")),
        installer_format: Some(installer_format),
    }
}

fn emit_event(app: &AppHandle, event: BootstrapEvent) {
    // Tee important state transitions to the rolling installer log so
    // bootstrap-installer.log isn't just "starting" + final summary.
    // Log lines (the noisy stuff) handle their own tracing in
    // run_install_script's sink; here we cover the lifecycle frames.
    match &event {
        BootstrapEvent::Manifest { stages, .. } => {
            tracing::info!(
                stage_count = stages.len(),
                names = ?stages.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
                "manifest received"
            );
        }
        BootstrapEvent::Stage {
            name,
            state,
            duration_ms,
            error,
            ..
        } => {
            tracing::info!(
                stage = %name,
                ?state,
                duration_ms = ?duration_ms,
                error = ?error,
                "stage transition"
            );
        }
        BootstrapEvent::Complete { install_root, .. } => {
            tracing::info!(install_root = %install_root, "bootstrap complete");
        }
        BootstrapEvent::Failed { stage, error } => {
            tracing::error!(stage = ?stage, error = %error, "bootstrap FAILED");
        }
        BootstrapEvent::Log { .. } => {
            // Log lines are teed via the sink callbacks in
            // run_install_script — don't double-emit here.
        }
    }
    if let Err(e) = app.emit(BootstrapEvent::CHANNEL, &event) {
        tracing::warn!(?e, "failed to emit bootstrap event");
    }
}

// Per-stage output caps for error preview lines. Stages typically produce
// tens of lines; the manifest is a single (possibly multi-line) JSON blob
// so it gets a larger window.
const STAGE_PREVIEW_CHARS: usize = 2000;
const MANIFEST_PREVIEW_CHARS: usize = 4000;

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}...", &s[..max])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn unique_tmp_dir(tag: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "deskagent-bootstrap-test-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&base).unwrap();
        base
    }

    /// Builds a fake "installed desktop" at the platform's canonical path
    /// (redirected to `install_root` via `set_desktop_root_override_for_test`)
    /// and returns the install_root. Mirrors the layout the slim install
    /// script's Stage-UnpackDesktop produces.
    fn make_installed_desktop(install_root: &Path) -> PathBuf {
        if cfg!(target_os = "macos") {
            let macos_dir = install_root
                .join("Contents")
                .join("MacOS");
            std::fs::create_dir_all(&macos_dir).unwrap();
            std::fs::write(macos_dir.join("DeskAgent"), b"#!/bin/sh\n").unwrap();
        } else {
            std::fs::write(install_root.join("DeskAgent.exe"), b"stub").unwrap();
        }
        install_root.to_path_buf()
    }

    /// The relaunch target is the platform-canonical installed desktop.
    /// On macOS this MUST resolve to the .app bundle (what `open` relaunches
    /// and what electron-updater replaces at /Applications/DeskAgent.app). A
    /// regression in this derivation breaks the post-install auto-relaunch,
    /// so guard it.
    #[test]
    fn resolve_deskagent_desktop_app_finds_installed_bundle() {
        let root = unique_tmp_dir("app-ok");
        set_desktop_root_override_for_test(root.clone());
        make_installed_desktop(&root);

        let resolved = resolve_deskagent_desktop_app()
            .expect("should resolve the installed desktop app");

        #[cfg(target_os = "macos")]
        {
            assert_eq!(
                resolved.extension().and_then(|e| e.to_str()),
                Some("app"),
                "relaunch target must be a .app bundle on macOS"
            );
            assert!(resolved.is_dir(), "macOS resolution must be a directory");
        }
        #[cfg(not(target_os = "macos"))]
        {
            assert!(resolved.is_file(), "non-macOS resolution must be a file");
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_deskagent_desktop_app_is_none_without_install() {
        let root = unique_tmp_dir("app-none");
        set_desktop_root_override_for_test(root.clone());
        // No installed desktop created.
        assert!(
            resolve_deskagent_desktop_app().is_none(),
            "no resolved app when nothing has been installed"
        );
        let _ = std::fs::remove_dir_all(&root);
    }
}
