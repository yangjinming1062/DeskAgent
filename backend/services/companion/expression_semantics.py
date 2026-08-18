"""Builtin emotion → expression clause for chat avatar generation.

21 entries — the 22-item ``BUILTIN_EMOTIONS`` minus ``neutral`` (neutral is
the portrait itself and never generates). Clauses describe the emotional
intent in human-readable terms; expression_avatar_service pairs them with a
single generic template — identity rides the reference image (reinforced by
the persona's core-features text), with the outfit (the wardrobe-mirrored
persona field) and personality colouring riding along, so the clause is the
only per-emotion content in the prompt. This module is the generation-side
authority; the client's EMOTION_MAP / sprite-semantics tables are
display-only and intentionally not shared. Custom CompanionExpression tokens
fall back to their registry description at the call site."""

EXPRESSION_SEMANTICS: dict[str, str] = {
    "happy": "开心地笑，眼角弯起",
    "excited": "兴奋雀跃，眼睛发亮，笑容灿烂",
    "grateful": "感激地微笑，眼神温暖真诚",
    "playful": "顽皮地做鬼脸，俏皮地眨眼",
    "proud": "骄傲自豪，下巴微抬，神情自信",
    "smug": "得意洋洋，嘴角上扬露出小得意的笑",
    "relieved": "如释重负地松了一口气，安心舒展的微笑",
    "shy": "害羞脸红，眼神躲闪带着羞涩的微笑",
    "curious": "好奇地睁大眼睛，兴致盎然",
    "surprised": "惊讶地睁大眼睛，微微张嘴",
    "confused": "疑惑不解，眉头微皱，歪着头",
    "concerned": "关切担忧，眉宇间带着挂念",
    "embarrassed": "尴尬地讪笑，不知所措",
    "apologetic": "不好意思地道歉，神情愧疚内疚",
    "sad": "难过低落，嘴角下垂，眼中含着失落",
    "lonely": "孤单委屈，神情落寞略带哀伤",
    "bored": "百无聊赖，眼神放空无精打采",
    "sleepy": "困倦地打哈欠，眼皮半阖",
    "pout": "气鼓鼓地噘嘴，脸颊鼓起，傲娇地别开视线",
    "angry": "生气恼怒，眉头紧锁，嘟着嘴",
    "scared": "受惊害怕，神情惊惶",
}
