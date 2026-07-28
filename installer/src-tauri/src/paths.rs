//! Filesystem paths + logging setup.
//!
//! Mirrors `deskagent_constants.get_deskagent_home()` from the Python CLI:
//!   Windows: %LOCALAPPDATA%\deskagent
//!   macOS:   ~/.deskagent
//!   Linux:   ~/.deskagent  (override via $DESKAGENT_HOME)
//!
//! NOTE (macOS): Python's get_deskagent_home(), installer/install.sh, and the
//! Electron desktop's resolveDeskAgentHome() ALL use ~/.deskagent on macOS — there
//! is no ~/Library/Application Support branch anywhere else. An earlier
//! version of this file used Application Support, which drifted from every
//! other component: the installer wrote the install to one dir and the
//! desktop looked for it in another, so first launch never found the backend.
//!
//! IMPORTANT: this must match exactly. Drift here means install.ps1
//! writes to one place and the installer reads from another, breaking
//! the bootstrap-complete check.

use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Command;
use tracing_appender::non_blocking::WorkerGuard;

/// Returns the canonical DeskAgent home directory, respecting $DESKAGENT_HOME if set.
pub fn deskagent_home() -> PathBuf {
    if let Ok(override_path) = std::env::var("DESKAGENT_HOME") {
        if !override_path.trim().is_empty() {
            return PathBuf::from(override_path);
        }
    }

    #[cfg(target_os = "windows")]
    {
        // %LOCALAPPDATA%\deskagent — matches installer/install.ps1's $DeskAgentHome.
        if let Some(local_app_data) = dirs::data_local_dir() {
            return local_app_data.join("deskagent");
        }
    }

    // macOS + Linux + fallback: ~/.deskagent (matches Python get_deskagent_home(),
    // install.sh, and the Electron desktop's resolveDeskAgentHome()).
    if let Some(home) = dirs::home_dir() {
        return home.join(".deskagent");
    }

    // Last resort — current dir, almost certainly wrong but at least
    // doesn't panic.
    PathBuf::from(".deskagent")
}

pub fn log_dir() -> PathBuf {
    deskagent_home().join("logs")
}

pub fn log_path() -> PathBuf {
    log_dir().join("bootstrap-installer.log")
}

/// Stable location the installer copies itself to after a successful install.
/// The start-menu / desktop shortcuts can point users back to it for repair
/// runs. Lives directly under DESKAGENT_HOME so it survives repo checkout deletion.
///
/// On Windows this is `%LOCALAPPDATA%\deskagent\deskagent-setup.exe`; on other
/// platforms the extension differs but the directory is the same.
pub fn installer_dest() -> PathBuf {
    let name = if cfg!(target_os = "windows") {
        "deskagent-setup.exe"
    } else {
        "deskagent-setup"
    };
    deskagent_home().join(name)
}

/// Copy the currently-running installer binary to `installer_dest()` so the
/// start-menu / desktop shortcuts have a stable target.
///
/// No-ops (returns Ok) when the running exe is ALREADY the destination (a
/// prior copy), where copying onto ourselves would be a Windows sharing
/// violation. Best-effort: a failure here must not fail the install, so the
/// caller logs and continues.
pub fn copy_self_to_deskagent_home() -> std::io::Result<()> {
    let src = std::env::current_exe()?;
    let dest = installer_dest();

    // Skip if we're already running from the destination (a prior copy).
    // canonicalize both so symlinks / 8.3 short paths / case differences don't
    // trick us into a self-copy.
    let same = match (src.canonicalize(), dest.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => src == dest,
    };
    if same {
        tracing::info!(?dest, "installer already at destination; skipping self-copy");
        return Ok(());
    }

    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::copy(&src, &dest)?;
    repair_macos_installer_helper(&dest);
    tracing::info!(?src, ?dest, "copied installer to DESKAGENT_HOME");
    Ok(())
}

#[cfg(target_os = "macos")]
fn repair_macos_installer_helper(path: &Path) {
    // The staged helper may inherit quarantine from the downloaded installer.
    // The desktop's start-menu shortcut launches this exact file for repair
    // runs, so make it executable before LaunchServices/Gatekeeper reject it.
    let _ = Command::new("/usr/bin/xattr")
        .args(["-cr"])
        .arg(path)
        .status();

    let verify = Command::new("/usr/bin/codesign")
        .arg("--verify")
        .arg(path)
        .status();

    if !matches!(verify, Ok(status) if status.success()) {
        let _ = Command::new("/usr/bin/codesign")
            .args(["--force", "--sign", "-"])
            .arg(path)
            .status();
    }
}

#[cfg(not(target_os = "macos"))]
fn repair_macos_installer_helper(_path: &Path) {}

/// Where install.ps1 writes the bootstrap-complete marker (existence-only file
/// the Electron app also checks). Per main.cjs:
///   const BOOTSTRAP_COMPLETE_MARKER = path.join(ACTIVE_DESKAGENT_ROOT, '.deskagent-bootstrap-complete')
/// We don't always know ACTIVE_DESKAGENT_ROOT until install.ps1 reports it, so
/// this is a probe helper, not a definitive path.
#[allow(dead_code)]
pub fn likely_bootstrap_marker(install_root: &Path) -> PathBuf {
    install_root.join(".deskagent-bootstrap-complete")
}

/// Path to the python binary in the Runner's uv-managed venv. `None`
/// when neither candidate exists (uv may have dropped `python.exe`
/// vs `python3.exe` depending on the targeted version); callers
/// should treat `None` as "no healthy venv" and refuse accordingly.
///
/// Mirrors `runner/utils/path_helpers.py::find_python()`'s candidate
/// list so a venv that uv produced on Windows without `python.exe`
/// but with `python3.exe` still reports positive.
pub fn runner_venv_python() -> Option<PathBuf> {
    let root = deskagent_home().join("runner").join(".venv");
    let candidates: [PathBuf; 2] = if cfg!(target_os = "windows") {
        [
            root.join("Scripts").join("python.exe"),
            root.join("Scripts").join("python3.exe"),
        ]
    } else {
        [
            root.join("bin").join("python"),
            root.join("bin").join("python3"),
        ]
    };
    candidates.into_iter().find(|p| p.is_file())
}

/// Initializes tracing to bootstrap-installer.log under DESKAGENT_HOME/logs/.
/// Returns a guard that flushes the appender on drop — keep it alive for
/// the lifetime of the process.
pub fn init_logging() -> Option<WorkerGuard> {
    let dir = log_dir();
    if let Err(err) = std::fs::create_dir_all(&dir) {
        // No log dir → log to stderr only. Don't panic; the installer
        // should still be usable on an exotic filesystem.
        eprintln!("[deskagent-setup] could not create log dir {dir:?}: {err}");
        return None;
    }

    let file_appender = tracing_appender::rolling::never(&dir, "bootstrap-installer.log");
    let (non_blocking, guard) = tracing_appender::non_blocking(file_appender);

    let env_filter = tracing_subscriber::EnvFilter::try_from_env("DESKAGENT_BOOTSTRAP_LOG")
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));

    tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_writer(non_blocking)
        .with_ansi(false)
        .with_target(true)
        .init();

    Some(guard)
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_log_path() -> String {
    log_path().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn get_deskagent_home() -> String {
    deskagent_home().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let path = log_dir();
    app.opener()
        .open_path(path.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}
