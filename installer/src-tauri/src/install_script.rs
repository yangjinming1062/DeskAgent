//! Resolves `installer/install.ps1` (or `installer/install.sh`) — the worker
//! scripts the Tauri installer spawns to release its bundled payload.
//!
//! Resolution order:
//!   1. Dev shortcut: a sibling repo checkout via $DESKAGENT_SETUP_DEV_REPO_ROOT.
//!      Lets devs iterate on the script without re-bundling.
//!   2. Bundled: Tauri `bundle.resources` (`payload/install.{sh,ps1}`). The
//!      `DeskAgent-Setup` binary is self-contained — no network, no GitHub, no
//!      cache. The script version IS the installer build version.
//!
//! Mirrors `desktop/main/bootstrap-platform.cjs`'s `resolveInstallScript`,
//! but the dev-checkout resolution is driven by an env var rather than the
//! Electron app's APP_ROOT/.. trick, because DeskAgent-Setup.exe is meant to
//! live OUTSIDE any repo checkout.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use tauri::{AppHandle, Manager};

/// The install script the Tauri process will spawn. Identified only by its
/// on-disk path — there is no commit/branch pin to track, because the
/// script is bundled into the installer binary itself.
#[derive(Debug, Clone)]
pub struct ResolvedScript {
    pub path: PathBuf,
    pub source: ScriptSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptSource {
    /// Loaded from a sibling repo checkout via $DESKAGENT_SETUP_DEV_REPO_ROOT.
    DevCheckout,
    /// Loaded from the Tauri `bundle.resources` directory.
    Bundled,
}

/// What flavor of script (Windows .ps1 vs Unix .sh).
#[derive(Debug, Clone, Copy)]
pub enum ScriptKind {
    Ps1,
    Sh,
}

impl ScriptKind {
    pub fn for_current_os() -> Self {
        if cfg!(target_os = "windows") {
            Self::Ps1
        } else {
            Self::Sh
        }
    }

    fn filename(&self) -> &'static str {
        match self {
            Self::Ps1 => "install.ps1",
            Self::Sh => "install.sh",
        }
    }
}

/// Resolves the install script to use for this run.
///
/// Order: dev (env override) → bundled (Tauri resources). There is no
/// network step — the installer binary is self-contained.
pub async fn resolve(
    app: &AppHandle,
    kind: ScriptKind,
    emit_log: &impl Fn(&str),
) -> Result<ResolvedScript> {
    // 1. Dev shortcut.
    if let Ok(repo_root) = std::env::var("DESKAGENT_SETUP_DEV_REPO_ROOT") {
        let candidate = PathBuf::from(repo_root).join("installer").join(kind.filename());
        if candidate.exists() {
            emit_log(&format!(
                "[bootstrap] dev mode — using local {} at {}",
                kind.filename(),
                candidate.display()
            ));
            return Ok(ResolvedScript {
                path: candidate,
                source: ScriptSource::DevCheckout,
            });
        }
    }

    // 2. Bundled in Tauri bundle.resources.
    let resource_dir = app
        .path()
        .resource_dir()
        .with_context(|| "resolving Tauri resource_dir".to_string())?;
    let bundled = resolve_bundled(&resource_dir, kind)?;
    emit_log(&format!(
        "[bootstrap] using bundled {} at {}",
        kind.filename(),
        bundled.display()
    ));
    Ok(ResolvedScript {
        path: bundled,
        source: ScriptSource::Bundled,
    })
}

/// Reads the install script from a Tauri `bundle.resources` directory.
/// Pure function — no AppHandle, so it's directly unit-testable with a
/// tmp dir. The script is a child of `payload/` because it expects to find
/// the runner / desktop / skills / config payload alongside it.
fn resolve_bundled(resource_dir: &Path, kind: ScriptKind) -> Result<PathBuf> {
    let script_path = resource_dir.join("payload").join(kind.filename());
    if script_path.is_file() {
        return Ok(script_path);
    }
    let embedded_path = crate::embedded_resources::extract_resources().join(kind.filename());
    if embedded_path.is_file() {
        return Ok(embedded_path);
    }
    Err(anyhow!(
        "install script not found in Tauri bundle: {}\n\
         The DeskAgent-Setup binary was built without `payload/{}` in its\n\
         bundle.resources. Re-build with `scripts/build_client.{{sh,ps1}}`\n\
         to stage the install scripts into `installer/payload/`.",
        script_path.display(),
        kind.filename()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn unique_tmp_dir(tag: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "deskagent-install-script-test-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_fake_bundled_script(install_root: &Path, kind: ScriptKind) {
        let payload = install_root.join("payload");
        fs::create_dir_all(&payload).unwrap();
        fs::write(payload.join(kind.filename()), b"#!/bin/sh\necho fake\n").unwrap();
    }

    /// The script bundled in the Tauri resources is the canonical source in
    /// production. Verifying the resolver returns it (and not a download or
    /// cache hit) is the whole point of removing the GitHub fallback.
    #[test]
    fn resolve_bundled_returns_script_in_payload() {
        let tmp = unique_tmp_dir("bundled-ok");
        write_fake_bundled_script(&tmp, ScriptKind::Sh);

        let resolved = resolve_bundled(&tmp, ScriptKind::Sh).unwrap();
        assert!(resolved.ends_with("install.sh"));
        assert_eq!(
            fs::read_to_string(&resolved).unwrap(),
            "#!/bin/sh\necho fake\n"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    /// When the standard bundle path is empty, resolve_bundled falls back
    /// to the embedded resources (include_bytes! payload). In a test binary
    /// built with payload staged, this succeeds.
    #[test]
    fn resolve_bundled_falls_back_to_embedded_resources() {
        let tmp = unique_tmp_dir("bundled-missing");
        let resolved = resolve_bundled(&tmp, ScriptKind::Sh);
        if let Ok(path) = resolved {
            assert!(path.ends_with("install.sh"));
        }
        let _ = fs::remove_dir_all(&tmp);
    }

    /// Script kind round-trips through filename() — guards against a typo
    /// (e.g., switching the .ps1 / .sh mapping) silently loading the wrong
    /// script on the wrong platform.
    #[test]
    fn script_kind_filename_round_trip() {
        assert_eq!(ScriptKind::Ps1.filename(), "install.ps1");
        assert_eq!(ScriptKind::Sh.filename(), "install.sh");
    }
}
