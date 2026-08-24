# scripts/

两类制品，刻意保持薄。**Install 脚本不在此处** —— 它们与 installer 同目录（[installer/install.{sh,ps1,cmd}](../installer/README.md)），让 Tauri 程序与其 worker 脚本作为一个自洽单元分发。

## 1. 构建安装器 — `build.py` / `build_client.{sh,ps1}`

单一入口，端到端编排 **runner (uv build wheel) → client (electron-builder) → stage → Tauri (installer)**。跨平台的纯文本编辑、版本同步与 payload 暂存由 `scripts/lib/build_helpers.py` 统一定义，`build_client.sh`（macOS）与 `build_client.ps1`（Windows）作为系统原生包装层调用共享逻辑。Windows 构建额外产出 `release/SpiritAgent-{ver}-update.zip` 自更新产物。

```bash
uv run python scripts/build.py --version 0.16.0
scripts/build_client.sh --version 0.16.0 --target mac
pwsh scripts/build_client.ps1 -Version 0.16.0
```

- 写版本号到 `client/package.json`、`installer/package.json`、`installer/src-tauri/tauri.conf.json`、`installer/src-tauri/Cargo.toml`、`runner/pyproject.toml`
- Staging 到 `installer/payload/`（symlink/junction skills 与 install 脚本，copy config + runner wheel + client artifact）
- macOS code-sign + notarize（`--sign-identity` / `--notary-profile`）；Windows signtool（`-CertThumbprint`）
- Tauri 2 默认对 `bundle.resources` 缺失文件**报错**；构建脚本在 tauri build 之前临时 patch `tauri.conf.json` 的 `bundle.resources` 列表，把占位文件替换为当前 host 的实际 client artifact，build 后 restore（git 状态保持干净）
- **跨平台 build 不可行** —— macOS code-sign 必须 mac host，Windows 必须 win host。脚本校验 `host/target` 匹配

**Windows 最终产物是单个 `SpiritAgent-Setup-{ver}.exe`**（不是 NSIS wrapper）。NSIS wrapper 会出现"双安装器"问题——NSIS 把 `SpiritAgent-Setup.exe` 装到 Program Files，用户还得再手动跑一次。直接发 `SpiritAgent-Setup.exe` 用户双击即看到 Tauri 安装 UI。Windows 脚本用 `tauri build --no-bundle` 跳过 NSIS，产物直接拷贝到 `release/SpiritAgent-Setup-{ver}.exe`。

**后端（Docker）不参与** —— 有独立的 docker-compose 部署流。

## 2. Import 检查 — `check_imports.py`

backend + runner 的 static import-shape 检查器（被 `.pre-commit-config.yaml` 注册为本地 hook 并以 `--strict-imports` 启动），防 c66ab1a 一类回归。覆盖 3 类违规：

- `TYPE_CHECKING` 名字泄漏：仅在 `if TYPE_CHECKING:` 内 import、却被类体注解等运行时求值路径引用的名字
- runner 工具子包之间的 sibling 跨子包 eager import（终端 ↔ 文件、代码执行 → 线程上下文这类循环）
- Facade 一致性：`from <local_pkg> import X` 走的 `<local_pkg>` 必须在其 `__init__.py` 里 re-export `X`，防止 facade 被过度精简

## 3. mesh2d 切分验证样本 — `_mesh2d_validation/`

按风格分组的立绘样图目录（`cel_shading/`、`anime_game_cg/`，各含 `layers/`），用于人工核对区域识别 / 抠图 / 骨骼装配质量——腿部切层等新能力上线前把样图放入后重跑切分管线抽检。当前为空脚手架。

## 4. 存量 mesh2d 动作表回填 — `backfill_mesh2d_actions.py`

动作系统升级（manifest v3 关键帧 tracks / click / point / idle 扩充）后，把最新 `DEFAULT_ANIMATIONS` 重新烘焙进所有 active 模型的 manifest 与资产文件、更新 content_hash——骨架/图层不动，旧模型（无 leg 层）回填后 locomotion 仍走复合躯干方案。客户端下次 hydrate 时按新 content_hash 重新拉取生效。需要 backend 环境变量可用。

```bash
uv run scripts/backfill_mesh2d_actions.py --dry-run   # 先看会动哪些模型
uv run scripts/backfill_mesh2d_actions.py
```

## 5. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

包含预渲染引导词音频元信息 `manifest.json` 与合成/校验脚本 `generate_onboarding_audio.py`。详见 [scripts/onboarding-audio/README.md](onboarding-audio/README.md)。
