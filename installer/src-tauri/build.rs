use std::process::Command;

fn main() {
    // -----------------------------------------------------------------
    // 把安装脚本 pin 烘焙到构建产物中。BUILD_PIN_COMMIT / BUILD_PIN_BRANCH 由
    // bootstrap.rs 的 `option_env!()` 取为默认值；优先级 commit > branch。
    // commit pin 默认为关闭（按分支 HEAD 跟随）；发布构建可通过 SPIRITAGENT_BUILD_PIN_COMMIT 烘焙不可变 SHA。
    // 分支 pin 顺序：环境变量 > 当前 checkout HEAD；分支与 commit 都解析不到时打印 warning。
    // 检测到 .git/HEAD 变化时 rerun，避免切换分支后 stale pin。
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
        // 解析失败时明确警告，避免运行时因缺少 pin 直接报错；构建环境很可能配置有误。
        println!(
            "cargo:warning=spiritagent-bootstrap: no pin resolved at build time; binary will fail at runtime without SPIRITAGENT_SETUP_DEV_REPO_ROOT or runtime args"
        );
    }

    // 切换分支 / 显式 commit pin 解析到移动 ref 时 rerun；.git/HEAD 每次提交/切换都会变。
    let git_dir = locate_git_dir();
    if let Some(gd) = &git_dir {
        println!("cargo:rerun-if-changed={}/HEAD", gd.display());
        // .git/HEAD 常指向 ref（如 `ref: refs/heads/xx/yy`），同时监控 ref 自身，便于同一分支新提交也能 rerun。
        if let Ok(head) = std::fs::read_to_string(gd.join("HEAD")) {
            if let Some(rest) = head.trim().strip_prefix("ref: ") {
                println!("cargo:rerun-if-changed={}/{}", gd.display(), rest);
            }
        }
    }
    println!("cargo:rerun-if-env-changed=SPIRITAGENT_BUILD_PIN_COMMIT");
    println!("cargo:rerun-if-env-changed=SPIRITAGENT_BUILD_PIN_BRANCH");

    // -----------------------------------------------------------------
    // Tauri Windows manifest：声明 level="asInvoker"，避免 Windows 的安装器启发式要求 UAC 提权。
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
}

fn resolve_commit_pin() -> Option<String> {
    // commit pin 仅在 SPIRITAGENT_BUILD_PIN_COMMIT 显式设置时生效；缺省按分支 HEAD 跟随。
    let requested = std::env::var("SPIRITAGENT_BUILD_PIN_COMMIT").ok()?;
    let requested = requested.trim();
    if requested.is_empty() {
        return None;
    }
    // 把请求（SHA / tag / branch）解析到不可变 SHA，确保烘焙的 pin 可复现；`^{commit}` 用于把 tag 解引用到它指向的提交。
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
    // 仓库外构建时 git 解析可能失败；此时仅当请求本身已像 SHA 才接受，否则直接 panic，避免烘焙进不可解析的 ref。
    if is_sha(requested) {
        return Some(requested.to_string());
    }
    panic!(
        "SPIRITAGENT_BUILD_PIN_COMMIT={requested:?} could not be resolved to a commit \
         (git rev-parse failed and it is not a valid SHA)"
    );
}

/// 判断 `s` 是否看起来像缩写或完整 git SHA（7..=40 个十六进制字符）。
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
    // detached HEAD 返回 "HEAD"，并无有效分支可 pin；只保留 commit pin。
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
