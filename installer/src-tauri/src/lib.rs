//! DeskAgent Setup — Tauri entrypoint.
//!
//! Spawns a single window pointed at the React frontend (installer/src/).
//! All install-time work lives in `bootstrap.rs` and is invoked through the Tauri
//! commands registered at the bottom of `run()`.
//!
//! The Windows-subsystem strip lives on the binary crate (src/main.rs), not
//! here — a crate-level attribute on a lib doesn't propagate to the linker
//! flags of the executable that consumes it.

mod bootstrap;
mod embedded_resources;
mod events;
mod install_script;
mod powershell;
mod paths;

use std::sync::Arc;
use tokio::sync::Mutex;

/// Returns true when the args request a forced installer UI (repair/reinstall)
/// via `--reinstall` or `--repair`, which overrides the macOS launcher
/// fast-path so a broken install can be repaired. Arg-iterator generic so it's
/// unit-testable. Independent of any mode selection.
pub fn force_setup_from_args<I, S>(args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter()
        .any(|a| a.as_ref() == "--reinstall" || a.as_ref() == "--repair")
}

/// Process-wide install state, shared across Tauri commands.
///
/// The bootstrap is a one-shot, single-tenant process — we only need one
/// of these per window. `Arc<Mutex<...>>` lets command handlers grab it
/// without lifetime gymnastics.
pub struct AppState {
    pub bootstrap: Mutex<Option<bootstrap::BootstrapHandle>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            bootstrap: Mutex::new(None),
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Tracing → bootstrap-installer.log under DESKAGENT_HOME/logs/ so install
    // failures leave a trail for support. Console output also goes here in
    // debug builds.
    let _guard = paths::init_logging();

    // Escape hatch: `--reinstall`/`--repair` forces the installer UI even when
    // DeskAgent is already installed, so users can re-run setup to repair a broken
    // install instead of the launcher fast path silently relaunching the app.
    let force_setup = force_setup_from_args(std::env::args().skip(1));
    tracing::info!(force_setup, "DeskAgent installer starting");

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .manage(Arc::new(AppState::new()))
        .setup(move |app| {
            use tauri::Manager;

            // Launcher fast path (macOS only): a bare ("Install") launch when
            // DeskAgent is already installed should NOT show the installer or
            // rebuild — it should just open the app, so the /Applications
            // "DeskAgent" doubles as a normal launcher (first run installs, every
            // later run launches instantly). The window is kept hidden until
            // here via `"visible": false` so this path never flashes a window.
            //
            // Gated to macOS deliberately: on Windows/Linux the installer keeps
            // its existing behavior (Windows users relaunch via the Start
            // Menu/Desktop "DeskAgent" shortcuts that install.ps1 creates, and a
            // reliable detached relaunch there needs the DETACHED_PROCESS +
            // startup-grace handling used by launch_deskagent_desktop — out of
            // scope here). So this is a pure no-op on non-macOS.
            //
            // `--reinstall`/`--repair` opts out so a broken install can be
            // repaired by re-running setup instead of launching the bad app.
            if cfg!(target_os = "macos") && !force_setup {
                if bootstrap::deskagent_is_installed() {
                    match bootstrap::spawn_installed_desktop() {
                        Ok(()) => {
                            // Brief grace so the spawned app is registered
                            // before we exit (mirrors launch_deskagent_desktop).
                            std::thread::sleep(std::time::Duration::from_millis(200));
                            tracing::info!(
                                "deskagent already installed — relaunched desktop; exiting installer"
                            );
                            app.handle().exit(0);
                            return Ok(());
                        }
                        Err(err) => {
                            tracing::warn!(
                                ?err,
                                "relaunch of installed desktop failed; showing installer UI"
                            );
                        }
                    }
                }
            }
            // First run / repair install: reveal the UI.
            match app.get_webview_window("main") {
                Some(win) => {
                    if let Err(err) = win.show() {
                        tracing::error!(?err, "failed to show main installer window");
                    }
                }
                None => {
                    tracing::error!("main installer window not found; installer UI will not appear");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // Bootstrap lifecycle
            bootstrap::start_bootstrap,
            bootstrap::cancel_bootstrap,
            bootstrap::get_bootstrap_status,
            // Hand-off
            bootstrap::launch_deskagent_desktop,
            // Diagnostics
            paths::get_log_path,
            paths::get_deskagent_home,
            paths::open_log_dir,
        ])
        .run(tauri::generate_context!())
        .expect("error while running DeskAgent Setup");
}

#[cfg(test)]
mod tests {
    use super::force_setup_from_args;

    #[test]
    fn reinstall_and_repair_flags_force_setup() {
        assert!(force_setup_from_args(["--reinstall"]));
        assert!(force_setup_from_args(["--repair"]));
        assert!(force_setup_from_args(["--foo", "--repair", "--bar"]));
    }

    #[test]
    fn bare_or_unrelated_args_do_not_force_setup() {
        assert!(!force_setup_from_args(Vec::<String>::new()));
        assert!(!force_setup_from_args(["--foo", "bar"]));
    }
}
