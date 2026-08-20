# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目入口文档

- **[RULES.md](RULES.md)** — 项目规范（协作、代码、文档、Commit、平台支持策略）。开始任何编码前**先读**。
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — 系统物理架构与跨模块不变量（不包含实现细节）。
- **[DESIGN.md](DESIGN.md)** — 陪伴型桌面伙伴的产品设计（不包含代码路径级细节）。
- **[PROTOCOL.md](PROTOCOL.md)** — 跨模块协议契约（JSON-RPC 方法 / 枚举 / 事件 / 安全 / 凭据）。
- **[docs/MODEL_SPEC.md](docs/MODEL_SPEC.md)** — 3D 模型骨骼 / 动画 / 形态事实规范,也是供应商产出契约。
- **[docs/PIPELINE.md](docs/PIPELINE.md)** — 3D 模型生成的能力链编排与 SPEC 校验门。
- 各模块的 `README.md` — 处理具体模块前先读对应 README（实现细节、文件树、配置项）。
