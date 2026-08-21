//! 解析安装脚本路径：优先 $SPIRITAGENT_SETUP_DEV_REPO_ROOT 开发入口，其次 Tauri bundle.resources；
//! 安装器不联网，脚本版本即安装器构建版本。

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone)]
pub struct ResolvedScript {
    pub path: PathBuf,
    pub source: ScriptSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptSource {
    /// 来自 $SPIRITAGENT_SETUP_DEV_REPO_ROOT 指向的本地 checkout。
    DevCheckout,
    /// 来自 Tauri bundle.resources。
    Bundled,
}

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

/// 解析安装脚本：dev 入口（环境变量覆盖）→ bundle.resources；不自联网。
pub async fn resolve(
    app: &AppHandle,
    kind: ScriptKind,
    emit_log: &impl Fn(&str),
) -> Result<ResolvedScript> {
    // 1) 开发入口：通过环境变量指向 checkout，避免每次脚本改动都要重打包。
    if let Ok(repo_root) = std::env::var("SPIRITAGENT_SETUP_DEV_REPO_ROOT") {
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

    // 2) 生产路径：Tauri bundle.resources。
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

/// 从 Tauri bundle.resources 读取安装脚本；纯函数，便于用临时目录直接单测。
fn resolve_bundled(resource_dir: &Path, kind: ScriptKind) -> Result<PathBuf> {
    let script_path = resource_dir.join("payload").join(kind.filename());
    if script_path.is_file() {
        return Ok(script_path);
    }
    Err(anyhow!(
        "install script not found in Tauri bundle: {}\n\
         The SpiritAgent-Setup binary was built without `payload/{}` in its\n\
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
            "spiritagent-install-script-test-{tag}-{}-{}",
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

    /// 生产环境下 bundle.resources 中的脚本才是正解；本测试就是验证移除 GitHub 回退后解析器的首选路径。
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

    /// 防止 .ps1 / .sh 映射被写错，从而在错误的平台加载到错误的脚本。
    #[test]
    fn script_kind_filename_round_trip() {
        assert_eq!(ScriptKind::Ps1.filename(), "install.ps1");
        assert_eq!(ScriptKind::Sh.filename(), "install.sh");
    }
}
