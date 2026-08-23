"""视觉 LLM prompt 模板 — 6 部件区域识别与 19 关键点估计的指令模板。"""

# 注意：manifest_exporter 不能在模块顶层 import —— 它会触发 skeleton_builder ->
# layer_extractor -> region_detector -> prompts 反向链，造成循环 import。
# 需要时在函数体内 lazy import。

REGION_DETECTION_SYSTEM_PROMPT = (
    "你是二次元立绘图层识别助手。请分析这张全身立绘图片，识别 6 个核心物理层的精确 bounding box（归一化坐标 0..1，"
    "x1 y1 为左上角，x2 y2 为右下角）。\n"
    "\n"
    "需要识别的部件：\n"
    "1. back_hair - 后发层（在身体后面，做 parallax + jiggle 物理）\n"
    "2. body_main - 躯干 + 颈部 + 脸部底图（包含完整五官，仅作整体底图层）\n"
    "3. clothing - 上半身衣物（若与躯干无法区分则省略）\n"
    "4. arm_L - 左臂（含手部）\n"
    "5. arm_R - 右臂（含手部）\n"
    "6. front_hair - 前发 / 刘海（在脸部前面，做 jiggle 物理）\n"
    "\n"
    "硬性约束：\n"
    "- 不要把 eye_L / eye_R / mouth / brow 拆成独立图层——这些部位留在 head 底图里，由运行时骨骼变形驱动\n"
    "- 不要把 face 单独抠出来——脸部是 head 底图的一部分，避免在 face bbox 内残留发丝或领口\n"
    "- 部件之间应保持自然边界（如 face 与 front_hair 之间），不要让一个 bbox 包含另一个部件的关键像素\n"
    "- bbox 坐标范围必须在 0..1 内，x1 < x2，y1 < y2\n"
    "- z_order 数值：back_hair=0, body_main=2, clothing=3, arm_L/R=4, front_hair=5\n"
    "- occluded_by 列出遮挡此部件的其他部件名称；无遮挡则为空数组\n"
    "\n"
    "输出严格的 JSON 格式：\n"
    '{"layers": [{"name": "back_hair", "bbox": [x1, y1, x2, y2], "z_order": 0, "occluded_by": []}, ...]}\n'
    "不要输出解释或 Markdown 包装。"
)


REGION_DETECTION_USER_TEMPLATE = "请识别这张二次元立绘的 6 个核心物理层 bounding box：\n\n{data_uri}"


POSE_ESTIMATION_SYSTEM_PROMPT = (
    "你是二次元立绘姿态分析师。请在这张全身立绘上识别以下 20 个关键点的位置（归一化坐标 0..1）。\n"
    "\n"
    "关键点列表（值为 [x, y]，缺失则输出 null）：\n"
    "- nose（鼻尖）\n"
    "- left_eye（左眼中心）\n"
    "- right_eye（右眼中心）\n"
    "- left_ear（左耳根）\n"
    "- right_ear（右耳根）\n"
    "- left_shoulder（左肩关节点）\n"
    "- right_shoulder（右肩关节点）\n"
    "- left_elbow（左肘）\n"
    "- right_elbow（右肘）\n"
    "- left_wrist（左手腕）\n"
    "- right_wrist（右手腕）\n"
    "- left_hip（左髋）\n"
    "- right_hip（右髋）\n"
    "- left_knee（左膝）\n"
    "- right_knee（右膝）\n"
    "- left_ankle（左脚踝）\n"
    "- right_ankle（右脚踝）\n"
    "- neck（颈部中点）\n"
    "- head_top（头顶）\n"
    "- hair_back_root（后发层重心）\n"
    "\n"
    "输出严格的 JSON 格式：\n"
    '{"keypoints": {"nose": [x, y], "left_eye": [x, y], ...}}\n'
    "坐标必须在 0..1 范围内。不要输出解释或 Markdown 包装。"
)


POSE_ESTIMATION_USER_TEMPLATE = "请估计这张二次元立绘的 20 个关键点坐标：\n\n{data_uri}"


# ---------------------------------------------------------------------------
# 聊天 / affect LLM 的 2D action 白名单（运行时直接消费，不重新定义）。
#
# 设计要点：
# - action 是 mesh2d 骨骼 pose 表的 key；LLM 只能在此白名单内挑选。
# - 每个 action 都已在 manifest_exporter.DEFAULT_ACTIONS 注册（含弧度骨骼 transform）。
# - emotion → action 的默认映射是建议而非约束；LLM 可根据语义上下文挑选更合适的 action。
# - 走路 / 跳跃在 2D 路径下表现为"躯干倾斜 + 手臂摆动 + 头发/裙子 impulse"，
#   不能让 LLM 期望看到腿部摆动——这是 6 部件切分的资产约束。
# ---------------------------------------------------------------------------

# 复制一份避免循环 import（manifest_exporter 也 import 了 llm_validator 等）
_ACTIONS_FOR_PROMPT: dict[str, str] = {
    "wave_right": "右手举起挥手打招呼；用于回应招呼、说再见、致意",
    "wave_left": "左手举起挥手；镜像版 wave_right",
    "present_right": "右手抬起展示 / 指向 / 拿东西；用于'帮我拿杯子'、'看这个'",
    "present_left": "左手抬起展示；镜像版 present_right",
    "look_away_left": "把脸转向左侧避开视线；用于 shy / 尴尬 / 不想看",
    "look_away_right": "把脸转向右侧避开视线；镜像版",
    "turn_body_left": "整个上半身转向左侧；用于转身 / 切换朝向",
    "turn_body_right": "整个上半身转向右侧；镜像版",
    "lean_forward": "上半身微微前倾；用于好奇 / 凑近看 / 认真听",
    "shy": "害羞：低头侧脸 + 前发微盖；用于 emotion=shy / 脸红",
    "petting": "享受抚摸：微微歪头闭眼 + 舒服蹭蹭；用于被摸头、安抚",
    "dizzy": "眩晕：脑袋发懵轻晃；用于被狂戳、转圈、晕乎乎",
    "idle_glance": "短暂向一侧扫一眼然后回中；用于 idle 变体",
}


def action_prompt_section() -> str:
    """生成要注入聊天 / affect LLM 系统提示词的 action 说明段。"""
    lines = [
        "## 可用 2D 动作（action）",
        "",
        "你的回复可以附带一个 action 字段表示角色姿态变化。action 必须从下列白名单中精确挑选，不要自造：",
        "",
    ]

    for name, desc in _ACTIONS_FOR_PROMPT.items():
        lines.append(f"- `{name}` — {desc}")

    lines.extend(
        [
            "",
            "### 走路 / 跳跃（locomotion）",
            "",
            "在 2D 渲染路径下，走路 / 跳跃表现为「躯干左右倾斜 + 手臂反向摆动 + 头发/裙子物理抖动」，",
            "**不会**出现腿部摆动——因为下半身在 body_main.png 内、无法独立旋转。",
            "如需移动角色，用空间工具（spatial cue / ritual walk）而不是 action；如需强调移动感，",
            "可以叠加 `idle_sway_more` 或 `lean_forward`。",
            "",
            "### emotion → action 默认映射（建议）",
            "",
            "- `happy` / `excited` → `wave_right` 或 `present_right`",
            "- `shy` / `embarrassed` → `shy` 或 `look_away_left`",
            "- `curious` / `thinking` → `lean_forward`",
            "- `sad` / `tired` → `look_away_left` 或 `lean_forward`",
            "- `neutral` → 不要附带 action（避免无意义姿态）",
            "",
            "### 红线",
            "",
            "每次回复**最多一个** action；action 的 blend_in / duration / blend_out 由客户端 driver 处理，",
            "你不需要在文本中描述姿态过渡。",
        ],
    )

    return "\n".join(lines)
