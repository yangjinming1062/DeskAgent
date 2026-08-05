# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 协作规范

1. **阅读文档优先**：优先阅读[ARCHITECTURE.md](ARCHITECTURE.md)和[COMPANION_DESIGN.md](COMPANION_DESIGN.md)了解项目思路，处理具体模块前**先读**该模块 `README.md`。
2. **同步更新文档**：修改源码/结构/导出/架构后，**同一提交**里同步该模块及所有受影响的 `README.md`。

## 全局已知限制与平台支持策略

- **Backend**：通过 Docker 部署，仅在 Docker (Linux 基础镜像) 内运行，**无需维护 Windows / macOS 兼容性**。
- **Runner / Desktop / Installer**：仅在 **Windows** 与 **macOS** 上原生运行（**不支持 Linux**）。由于依赖了如 pty、原生 subprocess 等与系统紧密相关的操作，需重点关注 Windows 的兼容性。Windows 兼容性缓解措施记录在各自模块的 `README.md` 与 `runner/README.md` 中。

## Commit 消息规范

按 [`.gitmessage`](.gitmessage) 模板写。仓库根的 `.gitmessage` 是编辑器模板，`git config commit.template .gitmessage` 启用后 `git commit` 自动加载。

## 文档维护规范

1. **同步更新**：改实现方案/架构设计 → 同提交同步该模块及**所有受影响**的 README.md。
2. **只记读代码推不出的东西**：设计意图（为什么这样选）、跨文件契约（不变量、约束）、决策权衡、已知限制。代码本身的结构——文件树逐文件职责、类的方法列表、函数级数据流、类型签名、JSONB 列映射——读代码或 spec 文档即可得出，不写。
3. **描述当前状态，不写变化叙事**：不写"X 不再 Y"、"现在改为 Z"、"从 W 移到了 V"。直接陈述当前架构（"X 在 Y 处调用"）。git log / blame 负责历史。
4. **跨模块共享的设计放共同祖先**：多个子模块共用的设计决策，记在最近的共同祖先 README 中，不在每个子模块重复。子模块 README 只记该模块**独有**的决策。

**写完一段后自检：删掉这段，读者会不会误解架构、违反约束、或做出错误决策？不会 → 这段是代码描述，删掉。**

| 类别 | 写 | 不写 |
|------|----|----|
| 设计意图 | 为什么这样选、有哪些权衡 | — |
| 行为契约 | 协议级不变量、关键约束 | 类名/方法签名/字段类型 |
| 业务规则 | 跨模块数据契约、状态转换规则 | 逐字段列映射 |
| 安全/隐私 | API Key fingerprinting、路径白名单 | 具体 schema |
| 已知限制 | 已知坑、临时绕过方案 | 注释里已写的 TODO |
| 架构地图 | 层/包的边界与依赖方向（≤5 行） | 逐文件列职责/导出列表 |
| 数据流 | 层/模块之间的数据流（架构级） | 逐函数调用链 |
| spec 内容 | — | ✓ 留给 spec 文档 |
| 代码块 | — | ✓ 留给源文件 |

## 代码规范

> 项目级强制约束，适用于所有子项目、所有语言（Python / JS / TS / CJS）。通用原则对每种语言同等生效；语言专属规则见末尾各小节。

### 高效、简洁

- **一行胜两行。** 多行表达式能合并成一行且读起来同样清晰，就合并。trivial 的 `if/else` 写成三元 / 条件表达式。
- **文档注释别复述函数名。** docstring / JSDoc 解释的是 *不显然的东西* —— 隐藏的约束、非平凡的返回语义、副作用。删掉后读者不会困惑，就别写。最多一行短句。不消歧义或不属于公共 API 的类型注解直接去掉。
- **trivial 的包装函数要内联。** 一个函数只是对另一个函数的一行透传且只有一处调用，就内联掉。不要增加没有收益的间接层。`def f(x): return g(x)` 就是噪音 —— 直接调用 `g(x)`。
- **不要保留未使用的参数。** 函数不用的形参就别收。如果分发器强制给所有回调塞额外参数，用剩余参数（Python `**_` / JS `...rest`）吸收 —— 但不要把参数穿过不需要它的层。
- **不要为了向后兼容写别名导入。** 重构代码时不要为了减少重命名而写别名（Python `from x import y as z` / JS `import { y as z }`）。直接使用原名，全量重命名。
- **资源获取即初始化。** 文件、锁、连接等必须用语言原生的资源管理惯用法获取和释放。Python 用 `with`；JS/TS 用 `try/finally` 或 `using` 声明。不要手动获取后靠 finally 勉强关闭。

### 死代码

- **立刻删除死代码。** 未使用的 import、未使用的常量、未使用的函数、未使用的参数 —— 全删掉。不要在生产代码里留 "以防万一" 或 "只在测试里用" 的包装，只在测试使用的代码也认为是死代码可以考虑连带无用的测试一起删除。
- **未使用的 import 别名也是死代码。** `import X as _Y` 但从不引用 `_Y`，删掉别名。`from Y import Z` 但 Z 在 import 行后面从不出现也一样。
- **没人调用的向后兼容垫片不要留。** 零活跃调用者的别名、为不存在的循环引用风险写的防御性重复 import、永远返回同一值的分支 —— 都是代码重量。确认不可达后删掉。

### 结构

- **重复是债，过度抽象也是债。** 同样的模式出现在 2+ 个同级函数里，或者同一个常量在 N 个模块里重复定义时，再抽共享 helper。不要第一次出现就抽 —— 等到第二个调用者出现，或者行数开始咬人。helper 放在最小的调用者旁边，而不是塞进通用的 `utils.js` / `utils.py`。
- **函数签名必须有显式类型标注。** 参数和返回值都要标注类型（Python 类型注解 / TS 返回类型签名）。明确的数据结构用语言原生的强类型定义（Python 用 Pydantic `BaseModel`，TS 用 `interface` / `type`），禁止用宽泛容器传递结构化数据（Python `dict`/`Any`；TS `any`/`Record<string, any>`）。这类宽泛类型只在对接外部不可控输入的边界处短暂使用。
- **异常只在系统边界捕获。** 只在用户输入、外部 API 调用、文件 IO 等系统边界处捕获异常并转换为业务错误；内部逻辑用 guard clause 快速失败（`if not valid: raise / throw`），不要用 try/catch 包裹大段代码来掩盖问题。

### 组织

- **import 之前不要写注释或文档。** import 块之前绝不要放字符串描述、横幅注释、`# === ...` / `// === ...` 分隔线。import 必须在最前；它之后第一行非 import 是顶层常量或函数声明。模块级的设计说明写在模块的 `README.md`，而不是文件顶部。写了会导致 pre-commit / eslint 出现 Failed。
- **import 必须放在模块顶部。** 不要写在函数内部。如果放在顶部会引发循环引用，那就重构代码（把共享部分抽到独立模块、调整依赖方向）来消除循环，而不是用 lazy import 临时绕开。
- **禁止越级深层导入。** 跨模块调用时，必须通过目标模块的公共入口（barrel file / `__init__`），绝不能直接深入到内部文件。正确：`from app.models import MessageType` / `import { MessageType } from '@/models'`；错误：`from app.models.message import MessageType` / `import { MessageType } from '@/models/message'`。
- **保持目录入口干净。** 入口目录只放入口、配置和数据模型；业务逻辑下沉到领域模块（`core/`、`tools/`、`routers/` 等，或 `renderer/companion/`、`renderer/hub/` 等）。
- **提交前先格式化。** Python 运行 `uvx pre-commit run -a`；Desktop 运行 `pnpm fix`（依次执行 `pnpm lint:fix` + `pnpm fmt` + `pnpm type-check`）。

### 注释

默认 **不写注释**。只有在有理由时才写：*为什么*（不显然的算法、反直觉的方向、设计权衡、库的坑），或者读者从代码本身推不出的约束。这条同时管内联注释、模块/类/schema 的 docstring，也管本文件本身的行文。

- **未来读者会不会卡住？** 如果只是省了 "读一行" 的事 → 删掉。
- **是 *为什么* 还是 *做什么*？** 如果只是复述代码 *做什么* → 删掉。永远不要复述 routing decorator、HTTP method、字段约束、类型签名或函数名。
- **最多一行。** 超过 ~3 行通常意味着代码应该被拆成更小的函数 / 更清楚的命名。
- **禁变化叙事与追踪编号。** 不写 "以前 X，现在 Y"（git log 负责）。不引用 issue 编号（`P0-16`、`M-5` 等）—— 这类编号在 PR 描述里有意义，在源码注释里是噪音。直接陈述当前状态。
- **注释不能替代文档。** 设计决策和已知限制写在模块的 README.md；如果新手看不懂，先修命名/结构 —— 注释是最后手段。

### 测试

- 覆盖核心功能、基本流程，不需要覆盖所有细节 —— 代码本身应该简单到读一遍就能确认正确。

### Python 专属

- **walrus 绑定一次性变量。** 条件分支里只用到一次的临时变量，用 `:=` 绑定，避免重复计算或额外占行。
- **生成器优先。** 处理大序列（视频帧、音频块、批量检测结果）时用生成器表达式和 `yield`，不要一次性收集到 `list` 里。`(process(f) for f in frames)` 比 `[process(f) for f in frames]` 省内存，功能等价时永远选生成器。
- **内建函数 & 向量化优先。** `sum`/`min`/`max`/`any`/`all`/`map`/`filter` 比手写循环更清晰也更快。数值密集操作用 NumPy/Pandas 批量完成，不要 Python 层逐元素遍历。
- **禁 `from __future__ import annotations`。** 目标是 Python 3.13 —— 这行没用。看到就删。
- **`__init__.py` 显式导出。** 每个子模块（如 `ai`, `models`, `services`, `schemas`）都应在 `__init__.py` 用 `__all__` 显式导出其对外的公共类、函数和常量，不要在单独的 py 文件中设置 `__all__`，那样没意义。
- **包内用相对 import。** 同一模块内部的文件互相引用时，用相对 import（`from .foo import bar`）让包自包含；只有跨包才用绝对 import（如 `from apps.auth import ...`）。**例外**：`engines/` 包内跨子包（`SLJ`↔`common`↔`MEDBALL`↔`pose`）统一使用绝对 import（`from engines.common import X`），不适用相对 import 规则——绝对路径在 IDE 导航/grep 中更友好，且 `engines` 作为一个整体安装包，子包间不存在独立发布场景。

### JS / TS 专属（Desktop）

- **显式标注函数返回类型。** 所有导出函数签名标注返回类型；禁止 `any`（对接外部不可控响应的边界处短暂使用 `unknown` + 类型守卫）。
- **import 排序与 JSX 属性排序由 ESLint perfectionist 规则强制。** 提交前确保 `pnpm lint` 无 error；可自动修复的用 `pnpm lint -- --fix`。
- **资源清理用 `useEffect` 返回值或 `try/finally`。** 定时器、事件监听、MediaStream 等在组件卸载或函数退出时必须释放。
