use std::io::Write;
use std::process::Command;

fn main() {
    // -----------------------------------------------------------------
    // Bake the install.ps1 pin into the binary at compile time.
    //
    // BUILD_PIN_COMMIT and BUILD_PIN_BRANCH are read by bootstrap.rs's
    // `option_env!()` macro to default the install-script reference.
    // Precedence (matches install.ps1's own arg precedence): commit > branch.
    //
    // The COMMIT pin is opt-in. By default a dev build pins ONLY the branch,
    // so the produced installer follows that branch's HEAD at install time
    // (tolerant of fast-forwards/new commits, and never references a SHA the
    // local checkout hasn't pushed). Set SPIRITAGENT_BUILD_PIN_COMMIT to bake an
    // immutable commit pin for reproducible/release installers.
    //
    // Commit pin resolution:
    //   - SPIRITAGENT_BUILD_PIN_COMMIT, if set and non-empty. Accepts a SHA, tag,
    //     or branch name; resolved to an immutable SHA via `git rev-parse`
    //     when possible, else used verbatim if it already looks like a SHA.
    //   - Otherwise: NO commit pin (branch-follow is the default).
    //
    // Branch pin resolution:
    //   1. SPIRITAGENT_BUILD_PIN_BRANCH, if set and non-empty.
    //   2. `git rev-parse --abbrev-ref HEAD` of the checkout this build.rs
    //      lives in — the current branch. (None on a detached HEAD.)
    //   3. Last-resort fallback handled below: if neither commit nor branch
    //      resolves, warn — the binary needs a runtime arg or dev-repo env.
    //
    // Build script reruns on git HEAD change so a new commit triggers
    // a rebuild without `cargo clean`.
    // -----------------------------------------------------------------

    let commit = resolve_commit_pin();
    let branch = resolve_branch_pin();

    if let Some(c) = &commit {
        println!("cargo:rustc-env=BUILD_PIN_COMMIT={c}");
        println!(
            "cargo:warning=spiritagent-bootstrap: pinning to commit {}",
            short(c)
        );
    }
    if let Some(b) = &branch {
        println!("cargo:rustc-env=BUILD_PIN_BRANCH={b}");
        match &commit {
            Some(_) => println!("cargo:warning=spiritagent-bootstrap: pinning to branch {b}"),
            None => println!(
                "cargo:warning=spiritagent-bootstrap: following branch {b} HEAD (no commit pin; \
                 set SPIRITAGENT_BUILD_PIN_COMMIT for an immutable pin)"
            ),
        }
    }
    if commit.is_none() && branch.is_none() {
        // Fail loudly rather than silently produce a binary that errors
        // at runtime with "no install-script pin supplied". A build that
        // can't resolve a pin almost certainly indicates a misconfigured
        // build environment.
        println!(
            "cargo:warning=spiritagent-bootstrap: no pin resolved at build time; binary will fail at runtime without SPIRITAGENT_SETUP_DEV_REPO_ROOT or runtime args"
        );
    }

    // Rerun build.rs when HEAD moves. With branch-follow as the default the
    // baked commit no longer changes per-commit, but a branch *switch* changes
    // the detected branch name, so we still re-trigger. When an explicit
    // SPIRITAGENT_BUILD_PIN_COMMIT resolves a moving ref (tag/branch) to a SHA, a
    // HEAD move can also change that resolution. .git/HEAD changes on every
    // commit / branch switch / rebase.
    let git_dir = locate_git_dir();
    if let Some(gd) = &git_dir {
        println!("cargo:rerun-if-changed={}/HEAD", gd.display());
        // .git/HEAD often points at a ref (e.g. `ref: refs/heads/bb/gui`);
        // also watch the ref itself so a new commit on the same branch
        // re-triggers.
        if let Ok(head) = std::fs::read_to_string(gd.join("HEAD")) {
            if let Some(rest) = head.trim().strip_prefix("ref: ") {
                println!("cargo:rerun-if-changed={}/{}", gd.display(), rest);
            }
        }
    }
    println!("cargo:rerun-if-env-changed=SPIRITAGENT_BUILD_PIN_COMMIT");
    println!("cargo:rerun-if-env-changed=SPIRITAGENT_BUILD_PIN_BRANCH");

    // -----------------------------------------------------------------
    // Tauri windows manifest. See spiritagent-setup.manifest for rationale —
    // declares level="asInvoker" so Windows's installer-detection
    // heuristic doesn't refuse to launch us without UAC elevation.
    // -----------------------------------------------------------------
    #[cfg(target_os = "windows")]
    let attrs = {
        let manifest = include_str!("spiritagent-setup.manifest");
        let win = tauri_build::WindowsAttributes::new().app_manifest(manifest);
        tauri_build::Attributes::new().windows_attributes(win)
    };

    #[cfg(not(target_os = "windows"))]
    let attrs = tauri_build::Attributes::new();

    tauri_build::try_build(attrs).expect("failed to run tauri-build");

    embed_payload();
}

fn embed_payload() {
    let manifest_dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let payload_dir = manifest_dir.join("../payload");
    if !payload_dir.is_dir() {
        println!("cargo:warning=spiritagent-bootstrap: payload/ not found; skipping resource embedding");
        return;
    }

    let out_dir = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let out_file = out_dir.join("embedded_resources.rs");
    let mut f = std::fs::File::create(&out_file).unwrap();

    let prefix = relative_path_prefix(&out_dir, &payload_dir);

    writeln!(f, "// Auto-generated by build.rs — do not edit").unwrap();
    writeln!(f).unwrap();
    writeln!(f, "const FILES: &[(&str, &[u8])] = &[").unwrap();

    let mut file_count: u32 = 0;
    for entry in walkdir::WalkDir::new(&payload_dir)
        .follow_links(true)
        .sort_by_file_name()
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let abs = entry.path();
        let rel = abs.strip_prefix(&payload_dir).unwrap();
        let rel_str = rel.to_string_lossy().replace('\\', "/");
        let include_path = format!("{}{}", prefix, rel_str);
        writeln!(f, "    (r#\"{}\"#, include_bytes!(r#\"{}\"#)),", rel_str, include_path).unwrap();
        file_count += 1;
    }

    writeln!(f, "];").unwrap();
    writeln!(f).unwrap();

    writeln!(f, r#"
static EXTRACTED_DIR: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();

pub fn extract_resources() -> &'static std::path::Path {{
    EXTRACTED_DIR.get_or_init(|| {{
        let tmp = std::env::temp_dir().join("spiritagent-setup-payload");
        let _ = std::fs::remove_dir_all(&tmp);

        for (rel, data) in FILES {{
            let path = tmp.join(rel);
            if let Some(parent) = path.parent() {{
                std::fs::create_dir_all(parent).unwrap();
            }}
            std::fs::write(&path, data).unwrap();
        }}

        tracing::info!(path = %tmp.display(), count = FILES.len(), "extracted embedded payload");
        tmp
    }}).as_path()
}}
"#).unwrap();

    println!("cargo:rerun-if-changed={}", payload_dir.display());
    println!("cargo:warning=spiritagent-bootstrap: embedded {file_count} payload files");
}

fn relative_path_prefix(from: &std::path::Path, to: &std::path::Path) -> String {
    let from_abs = std::fs::canonicalize(from).unwrap_or_else(|_| from.to_path_buf());
    let to_abs = std::fs::canonicalize(to).unwrap_or_else(|_| to.to_path_buf());

    let from_comps: Vec<_> = from_abs.components().collect();
    let to_comps: Vec<_> = to_abs.components().collect();

    let mut diverge = 0;
    while diverge < from_comps.len() && diverge < to_comps.len() && from_comps[diverge] == to_comps[diverge] {
        diverge += 1;
    }

    let ups = from_comps.len() - diverge;
    let mut prefix = String::new();
    for _ in 0..ups {
        prefix.push_str("../");
    }
    for comp in &to_comps[diverge..] {
        prefix.push_str(&comp.as_os_str().to_string_lossy());
        prefix.push('/');
    }
    prefix
}

fn resolve_commit_pin() -> Option<String> {
    // Commit pinning is OPT-IN. Only bake a commit when the caller explicitly
    // asks for one via SPIRITAGENT_BUILD_PIN_COMMIT. With no env var, we return
    // None and the installer follows the branch HEAD at install time.
    let requested = std::env::var("SPIRITAGENT_BUILD_PIN_COMMIT").ok()?;
    let requested = requested.trim();
    if requested.is_empty() {
        return None;
    }
    // Resolve the request (which may be a SHA, tag, or branch name) to an
    // immutable commit SHA so the baked pin is reproducible. `^{commit}`
    // dereferences tags to the commit they point at.
    if let Ok(out) = Command::new("git")
        .args(["rev-parse", "--verify", &format!("{requested}^{{commit}}")])
        .output()
    {
        if out.status.success() {
            if let Ok(s) = String::from_utf8(out.stdout) {
                let s = s.trim().to_string();
                if !s.is_empty() {
                    return Some(s);
                }
            }
        }
    }
    // Couldn't resolve via git (e.g. building outside a checkout). Accept the
    // literal value only if it already looks like a SHA; otherwise fail loud
    // rather than bake an unresolvable ref into the binary.
    if is_sha(requested) {
        return Some(requested.to_string());
    }
    panic!(
        "SPIRITAGENT_BUILD_PIN_COMMIT={requested:?} could not be resolved to a commit \
         (git rev-parse failed and it is not a valid SHA)"
    );
}

/// True if `s` looks like an abbreviated-or-full git SHA (7..=40 hex chars).
fn is_sha(s: &str) -> bool {
    let len = s.len();
    (7..=40).contains(&len) && s.chars().all(|c| c.is_ascii_hexdigit())
}

fn resolve_branch_pin() -> Option<String> {
    if let Ok(v) = std::env::var("SPIRITAGENT_BUILD_PIN_BRANCH") {
        if !v.trim().is_empty() {
            return Some(v.trim().to_string());
        }
    }
    let out = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    // "HEAD" is what you get on a detached checkout — no meaningful branch
    // to pin to. The commit pin still applies; just don't emit a branch.
    if s.is_empty() || s == "HEAD" {
        None
    } else {
        Some(s)
    }
}

fn locate_git_dir() -> Option<std::path::PathBuf> {
    let out = Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    if s.is_empty() {
        return None;
    }
    Some(std::path::PathBuf::from(s))
}

fn short(commit: &str) -> &str {
    if commit.len() >= 12 {
        &commit[..12]
    } else {
        commit
    }
}
