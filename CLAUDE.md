# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## AI 协作规范

1. **阅读文档优先**：处理模块前**先读**该模块及父级 `CLAUDE.md`。
2. **同步更新文档**：修改源码/结构/导出/架构后，**同一提交**里同步该模块及所有受影响父级的 `CLAUDE.md`。
3. **文档分层**：每层 CLAUDE.md 只记**读代码不能直接得出**的内容——设计意图、跨文件契约、决策权衡、业务规则等。依赖、文件树、导出列表、类型签名等留给代码自解释（详见"文档分层与维护规范"）。

## 全局已知限制与平台支持策略

- **Backend**：通过 Docker 部署，仅需保障 Linux (Docker) 容器内运行正常，**无需维护 Windows 兼容性**。
- **Runner / Desktop / Installer**：需要在 Windows、macOS 和 Linux 上原生运行。由于依赖了如 pty、原生 subprocess 等与系统紧密相关的操作，需高度关注这三个平台（特别是 Windows）的兼容性。相关的 Windows 兼容性缓解措施均记录在各自模块的 `CLAUDE.md` 与 `runner/CLAUDE.md` 中。

## 架构文档

项目级架构总览（三模块分工、通信模型、安全边界、协议级不变量）位于根目录 [design.md](design.md)。各模块的行为契约与设计权衡记录在对应子目录的 `CLAUDE.md` 中，见下方"模块导航"。

## 模块导航

| 模块 | 路径 | CLAUDE.md |
|------|------|-----------|
| Backend | `backend/` | [CLAUDE.md](backend/CLAUDE.md) |
| Runner | `runner/` | [CLAUDE.md](runner/CLAUDE.md) |
| Desktop | `desktop/` | [CLAUDE.md](desktop/CLAUDE.md) |
| Installer | `installer/` | [CLAUDE.md](installer/CLAUDE.md) |
| Scripts | `scripts/` | [CLAUDE.md](scripts/CLAUDE.md) |

## 文档分层与维护规范

每层 CLAUDE.md 职责：根目录记**项目级**（设计思想、架构、全局流程、文档规范本身），子目录记**模块级**（模块决策、跨文件契约等）。

| 类别 | 写 | 不写 |
|------|----|----|
| 设计意图 | 为什么这样选、有哪些权衡 | — |
| 行为契约 | 三态语义、错误分流、关键参数阈值 | 类名/方法签名 |
| 业务规则 | 协议级不变量 | 字段类型 |
| 安全/隐私 | API Key fingerprinting、路径白名单 | 具体 zod schema |
| 已知限制 | 已知坑、临时绕过方案 | 注释里已写的 TODO |
| 文件树（带每个文件职责） | ✓（模块级 CLAUDE.md）— 跨子包/跨层契约的快速地图 | 单纯目录罗列而不带职责说明 |
| 依赖 / 导出列表 | × | ✓ 留给 package.json / index.ts |
| 重复代码块 | × | ✓ 留给源文件 |

**维护规则**：

1. 改实现方案/架构设计 → 同提交同步该模块及**所有受影响父级** CLAUDE.md。
2. 新写 CLAUDE.md 用上表自检。
3. 设计意图最丰富的模块要让审阅者**只看文档**就能答关键设计问题。
4. **描述当前状态，不记录"反应变化"的叙述**。改完代码后写的描述应当让从未看过旧版本的读者也能直接理解：避免"X 不再 Y"、"现在改为 Z"、"从 W 移到了 V"这类变化叙事；用直接陈述当前架构的句式（"X 在 Y 处调用"）替代。git log / blame 负责解释历史，CLAUDE.md 描述当下。

## 注释编写原则

**默认不写注释。** 写必须有理由——"为什么"（不显然的算法 / 违反直觉的方向 / 设计权衡 / 库陷阱），或读代码无法自然得出的约束。

适用范围：inline 注释、module/类 docstring、schema docstring、CLAUDE.md 自身内容——全部按本节规则。

1. 删掉后**未来读者会卡在哪**？只是"少读一行字" → 删。
2. 是"为什么"还是"是什么"？只是"是什么" → 删。
3. **是否在复述 routing decorator / HTTP method / Pydantic 字段约束 / 类型签名 / 函数名**？是 → 删。
4. 能 1 行写完吗？超 3 行通常意味着该重构成更小的函数/更清晰的命名。

### 注释不是文档的替代

- **设计决策** → 写进模块 CLAUDE.md
- **陷阱警告** → 写进 CLAUDE.md 已知限制小节
- **一段不显然的代码** → 1 行注释就够，不长篇大论
- **新人不理解** → 优先改进命名/结构/拆分，注释是最后手段


## 代码编写规范

为保持Python代码结构清晰和高可维护性，遵循以下原则：
1. **模块导出封装 (`__init__.py`)**：在子目录的 `__init__.py` 中统一和显式地导出公共接口。禁止 Facade 导入。
2. **相对导入优先**：在同一个包/子目录内部，优先使用相对导入（如 `from .foo import bar`）来引用同级文件，提升模块内部的内聚性，避免硬编码顶层包名。
3. **保持根目录整洁**：除了入口文件、配置文件、数据库和全局数据模型之外，业务逻辑必须下沉到各自领域模块（如 `core/`, `tools/`, `routers/`）中。
4. **禁止在文件头的导入语句前写描述性的字符串**
5. 代码编写完成后使用`uv run pre-commit run -a`对代码进行格式化