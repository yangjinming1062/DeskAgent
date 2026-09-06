# `services/chat/`

Backend 单次对话回合的编排核心：把“系统指令 + 用户输入 + 历史 + 工具 schema”组装成 Responses 上下文喂给 LLM，对流式事件做脱敏与情绪采集，把工具调用下发给本地 Runner，等回灌后再次送入模型直到 budget 用尽或 LLM 自决终止，最后把 assistant 回复持久化并下发对话完成事件。

架构上下文与跨模块契约见 [ARCHITECTURE.md §4.2.I / §6.3 / §6.4](../../docs/ARCHITECTURE.md)；本文件只记 chat 包内独有的设计决策与边界。

## 模块地图

```
chat/
├── orchestrator.py          # run_chat_turn：单回合主循环（压缩、budget、guardrail、affect 透传）
├── turn_inputs.py           # 组装 system prompt（persona + user_profile + memory）+ schemas + LLM client
├── streaming.py             # 单次 LLM call 全流程：chunk → 影响 scrubber + usage → message.* 帧
├── affect.py                # AffectScrubber（流式剥离 `[affect:…]` tag）
├── persistence.py           # Message 行落库；触发 title 生成 / background review；emit message.complete
├── tool_dispatch.py         # 并行 dispatch + return_exceptions 容错；并行白名单见 services/tools/tool_dispatch_helpers
├── system_prompt.py         # 全部 prompt 模板（identity / persona / outfit / user_profile / language / task / steer / 平台 / volatile）
├── message_sanitization.py  # JSON 修复 + 截图归一化 + Responses 输入项窗口兜底
├── chat_emitter.py          # Emitter Protocol + HeadlessEmitter（子 agent）
├── agent_delegate.py        # agent_delegate_tool：spawn 子 agent 跑完整 chat-turn，HeadlessEmitter 捕获帧、提取最终答案作为工具结果返回
├── types.py                 # IterationBudget（threading.Lock 计数）+ TrackTask 类型别名
└── __init__.py              # 包公共入口（eager import）
```

## 关键不变量（chat 包内独有）

- **affect 双通道**：chat 回合只在对话完成事件内联情绪字段；独立的伙伴情绪事件归 `services/companion/affect_emit.py`，不在 chat 路径（事件契约见 [PROTOCOL.md](../../../docs/PROTOCOL.md)，详见 [ARCHITECTURE.md §4.2.IV](../../docs/ARCHITECTURE.md)）。
- **iteration budget 双层**：`IterationBudget(max_total=AGENT_MAX_LOOP_TURNS=150)` 计数 + `ToolCallGuardrailController.halt_decision` 语义提前退出。任一触发即停。
- **provider fallback 边界**：`execute_with_fallback` 的 `on_first_chunk` 哨兵防止 mid-stream 切换 provider——一旦已开始向 renderer 流式输出，下一 call 就锁死在当前 provider，避免同一回合混合两个模型的输出。
- **Responses 边界**：持久化层保留按角色建模的消息行；仅在读历史、工具回灌与后台 LLM 调用时转换为指令区 + 输入项。同一工具回合内的推理输出项保留到函数调用与输出闭合，近期图片载荷按二进制附件预算处理，不参与长文本截断。
- **影响 scrubber 在流式阶段就解析**：scrubber 在 chunk 层面拆情绪标签，orchestrator 拿到完整情绪在回合结束；这与 ARCH §6.3 "情绪基调先于语音"一致——desktop 收到对话完成事件时情绪字段已就位，TTS/EMOTIONAL 切换一次到位。
- **image part 单一来源**：`message_sanitization._IMAGE_PART_TYPES = {"input_image"}` 是 Responses API 输入图片 part 唯一类型；`persistence._build_persisted_content_from_parts` 写入与 `_input_part` 读取两侧一致。
- **生成媒体在回合收口提取、随终端助手行落库**：`persistence.extract_turn_media` 只认图像/视频生成工具的成功结果（pending 与失败跳过），媒体列与正文正交且不进 LLM 上下文（URL 已在工具结果/摘要行内）；多气泡回合媒体挂最后一格。为什么不信任正文贴 URL：模型会漏贴或夹带 markdown，结构化提取是渲染端唯一可靠通道。跨模块契约见 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)。
- **陪伴对话的时间感知不写进消息正文**：时间元数据不落库，跨轮按发送时刻重建以保留 prefix cache。陪伴预设的系统提示词不放当前日期。scrubber 仍会剥掉溢出的时间提示。
- **流式 chunk 批处理**：在 5–10 ms 批窗口内合并连续 chunk 事件为单个 chunk 载荷；break 与 message.start 立即发出。

## 已知限制

无
