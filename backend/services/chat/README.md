# `services/chat/`

Backend 单次对话回合的编排核心：把"system prompt + 用户输入 + 历史 + 工具 schema"组装好喂给 LLM，对流式 chunk 做脱敏 / 情绪采集 / think-tag 剥离，把工具调用下发给本地 Runner，等回灌后再次送入模型直到 budget 用尽或 LLM 自决终止，最后把 assistant 回复持久化并 emit `message.complete`。

架构上下文与跨模块契约见 [ARCHITECTURE.md §4.2.I / §6.3 / §6.4](../../ARCHITECTURE.md)；本文件只记 chat 包内独有的设计决策与边界。

## 模块地图

```
chat/
├── orchestrator.py          # run_chat_turn：单回合主循环（压缩、budget、guardrail、affect 透传）
├── turn_inputs.py           # 组装 system prompt（persona + user_profile + memory）+ schemas + LLM client
├── streaming.py             # 单次 LLM call 全流程：chunk → 影响 scrubber + think scrubber + usage → message.* 帧
├── affect.py                # ALLOWED_EMOTIONS + AffectScrubber（流式剥离 `[affect:…]` tag）
├── think_scrubber.py        # StreamingThinkScrubber：流式过滤 `<think>` 标签
├── persistence.py           # Message 行落库；触发 title 生成 / background review；emit message.complete
├── tool_dispatch.py         # 并行 dispatch + return_exceptions 容错；并行白名单见 services/tools/tool_dispatch_helpers
├── system_prompt.py         # 全部 prompt 模板（identity / persona / user_profile / language / task / steer / 平台 / volatile）
├── history.py               # 从 DB Message 重构 OpenAI messages
├── message_sanitization.py  # JSON 修复 + 截图归一化 + truncate_chat_history（40 条窗）
├── chat_emitter.py          # Emitter Protocol + HeadlessEmitter（子 agent）
├── agent_delegate.py        # agent_delegate_tool：spawn 子 agent 跑完整 chat-turn，HeadlessEmitter 捕获帧、提取最终答案作为工具结果返回
├── types.py                 # IterationBudget（threading.Lock 计数）+ TrackTask 类型别名
└── __init__.py              # barrel + __getattr__ 懒加载 orchestrator / turn_inputs / agent_delegate
```

## 关键不变量（chat 包内独有）

- **affect 双通道**：chat 回合只在 `message.complete` 内联 `affect: {emotion}`；独立的 `companion.affect` 事件归 `services/companion/affect_emit.py`，不在 chat 路径（详见 [ARCHITECTURE.md §4.2.IV](../../ARCHITECTURE.md)）。
- **iteration budget 双层**：`IterationBudget(max_total=AGENT_MAX_LOOP_TURNS=150)` 计数 + `ToolCallGuardrailController.halt_decision` 语义提前退出。任一触发即停。
- **provider fallback 边界**：`execute_with_fallback` 的 `on_first_chunk` 哨兵防止 mid-stream 切换 provider——一旦已开始向 renderer 流式输出，下一 call 就锁死在当前 provider，避免同一回合混合两个模型的输出。
- **影响 scrubber 在流式阶段就解析**：`AffectScrubber.feed` 在 chunk 层面拆 tag，orchestrator 拿到完整 emotion 在 turn 结束；这与 ARCH §6.3 "情绪基调先于语音"一致——desktop 收到 `message.complete` 时 affect 字段已就位，TTS/EMOTIONAL 切换一次到位。
- **image part 单一来源**：`message_sanitization._IMAGE_PART_TYPES` 是 OpenAI/Anthropic/Gemini 图片 part 类型集合；tool dispatch 不重复定义，旧对话读回时由 `_trajectory_normalize_msg` 一处统一处理。
## 已知限制

- `_LAZY_SUBMODULES = ("orchestrator", "turn_inputs", "agent_delegate")` 列表是结构性的而非策划性；新符号加在 lazy 子模块里无需更新 `__init__`，`__getattr__` 按需解析。
