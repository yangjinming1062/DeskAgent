import contextlib
import logging
import re
import sys
import tempfile
import uuid
from pathlib import Path
from shutil import copy2
from typing import Any

from utils import get_spiritagent_dir

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None  # type: ignore[assignment]
if sys.platform == "win32":
    try:
        import pythoncom
    except ImportError:
        pythoncom = None  # type: ignore[assignment]
else:
    pythoncom = None  # type: ignore[assignment]

from ...registry import registry, tool_error, tool_result
from .audio_io import cleanup_audio_cache_dir
from .piper_runtime import (
    PIPER_VOICE_RE,
    _is_voice_installed,
    _runtime,
    bundled_voices,
    default_voice_id,
    ensure_voice_installed,
    list_installed_voices,
    pick_voice_for_text,
    piper_available,
    piper_voice_dir,
    pyttsx3_available,
    text_language,
)

logger = logging.getLogger(__name__)


TEXT_TO_SPEECH_SCHEMA = {
    "name": "text_to_speech",
    "description": (
        "Synthesize speech from text using local engines (Piper primary; "
        "pyttsx3 fallback). Returns the absolute path of the WAV file on "
        "the runner's audio cache. The caller (Desktop) plays it back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to speak. Markdown, emoji, and unit-glyph normalization are applied."},
            "voice": {
                "type": "string",
                "description": (
                    "Piper voice id (e.g. 'zh_CN-huayan-medium'). "
                    "Cloud ids (e.g. '冰糖', 'mimo_default') are not local — "
                    "the runner returns an error so the desktop can route to "
                    "/api/media/tts instead."
                ),
            },
            "engine": {"type": "string", "description": "Force a specific engine: 'piper' or 'pyttsx3'. Default: auto-pick installed.", "enum": ["piper", "pyttsx3", "auto"]},
            "speed": {"type": "number", "description": "Speech rate multiplier in [0.5, 2.0]. Default 1.0."},
        },
        "required": ["text"],
    },
}


LIST_VOICES_SCHEMA = {
    "name": "list_tts_voices",
    "description": ("Returns the list of Piper voice ids installed under $SPIRITAGENT_HOME/models/piper/. Useful for the user to pick a voice in [desktop/plan §6 Settings]."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


# 后端对外展示的云端语音 id — Piper/pyttsx3 无法直接合成。
# 用 tuple 让 str.startswith 一次性在 C 层检查所有前缀；新增提供商必须同时在此追加前缀并把语音接入
# backend/services/llm/voice_catalog.py — 保持两边一致是本列表的契约。
_CLOUD_VOICE_HINTS = ("mimo_", "minimax_", "minimax:")


def _is_cloud_voice(voice: str) -> bool:
    """voice 是云端提供方 id（非本地 Piper id）时返回 True。

    形状兜底（不匹配 PIPER_VOICE_RE 的全部归入云端）可捕获形如 `冰糖` / `Mia` 这种没有已知前缀的裸名；
    前缀列表是前瞻性兜底，用于拦截看似 Piper 形状但实际是云端 token 的 id（如 `mimo_voicedesign:<prompt>`）。
    """
    return voice.startswith(_CLOUD_VOICE_HINTS) or not PIPER_VOICE_RE.match(voice)


def _normalize_text(text: str) -> str:
    text = re.sub(r"<think>" + r".*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", text).strip()


def _output_path(name_hint: str = "tts") -> Path:
    base = get_spiritagent_dir("cache/audio/tts", "audio_cache")
    base.mkdir(parents=True, exist_ok=True)
    cleanup_audio_cache_dir(base)
    return base / f"{name_hint}_{uuid.uuid4().hex[:10]}.wav"


def _synth_piper(text: str, voice: str, speed: float, dst: Path) -> dict[str, Any]:
    # 模块单例使 voice LRU 真正跨调用保持 — 每次新建 PiperRuntime 都会重载 ONNX 模型
    return {"engine": "piper", "voice": voice, "path": str(_runtime.synthesize(text, voice_id=voice, output_wav=dst, speed=speed))}


def _enumerate_pyttsx3_voices(engine: Any) -> list[dict[str, str]]:
    """快照 pyttsx3 voices 列表为 {id, name, lang, lang_name, is_zh}。

    engine 由调用方持有 — 必须有可用的 pyttsx3.init() 句柄。
    Windows 上调用线程必须先调用 CoInitialize，否则 SAPI5 抛 OSError: CoInitialize has not been called。
    """
    try:
        voices = engine.getProperty("voices") or []
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for v in voices:
        vid = str(getattr(v, "id", "") or "").strip()
        name = str(getattr(v, "name", "") or "").strip()
        langs_attr = getattr(v, "languages", None) or []
        langs = []
        for item in langs_attr:
            if isinstance(item, bytes):
                try:
                    item = item.decode("utf-8", errors="replace")
                except Exception:
                    item = repr(item)
            langs.append(str(item))
        lang_primary = langs[0] if langs else ""
        lang_lower = " ".join([name.lower(), lang_primary.lower(), " ".join(langs).lower()])
        is_zh = any(tok in lang_lower for tok in ("chinese", "mandarin", "中文", "zh-cn", "zh_cn", "zh-hans", "cmn"))
        out.append({"id": vid, "name": name, "lang": lang_primary, "lang_name": ",".join(langs), "is_zh": "1" if is_zh else "0"})
    return out


# 模块级缓存：OS 语音列表运行时不变。SAPI5 枚举每次 init() 约 100ms；init 仍按调用进行（引擎的 runAndWait 生命周期持有 COM apartment）
_PYTTSX3_VOICE_CACHE: list[dict[str, str]] | None = None


def _synth_pyttsx3(text: str, voice: str | None, speed: float, dst: Path) -> dict[str, Any]:
    # Windows 上的 pyttsx3 通过 COM 调用 SAPI5。runner 的工具跑在 asyncio 工作线程，
    # 这些线程未初始化 COM — 若不引导 apartment 则 SAPI5 抛 OSError: [WinError -2147221008] CoInitialize has not been called。
    # CoInitialize / CoUninitialize 必须按线程成对调用。仅 Windows；导入延迟以避免影响 POSIX。
    # 语音枚举与合成都必须在此作用域内完成 — 见下面的 _PYTTSX3_VOICE_CACHE。
    com_initialized = False
    if sys.platform == "win32":
        try:
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            com_initialized = False

    try:
        engine = pyttsx3.init()
        try:
            rate = engine.getProperty("rate")
            if rate:
                engine.setProperty("rate", int(rate * speed))
        except Exception:
            pass

        # 在 COM 作用域内解析 voice。显式 caller id 优先；否则在 CJK 文本上挑一个中文 SAPI5 语音，但仅当 caller 未指定时（见 text_to_speech_tool）
        global _PYTTSX3_VOICE_CACHE
        if not voice:
            if _PYTTSX3_VOICE_CACHE is None:
                _PYTTSX3_VOICE_CACHE = _enumerate_pyttsx3_voices(engine)
            if text_language(text) == "zh":
                voice = next((v["id"] or None for v in _PYTTSX3_VOICE_CACHE if v["is_zh"] == "1"), None)

        # pyttsx3 的 setProperty("voice") 跨平台是尽力而为
        if voice:
            with contextlib.suppress(Exception):
                engine.setProperty("voice", voice)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
            copy2(tmp_path, dst)
        finally:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
    finally:
        if com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass

    return {"engine": "pyttsx3", "voice": voice or "(system default)", "path": str(dst)}


def _check_tts() -> bool:
    return piper_available() or pyttsx3_available()


def _all_engines_failed(last_error: Exception | None) -> str:
    return tool_error(
        f"all engines failed: {last_error}",
        hint=("Local Piper synthesis failed and pyttsx3 fallback also failed. Set tts.engine=cloud in Desktop settings to route to /api/media/tts."),
        success=False,
    )


def text_to_speech_tool(args: dict[str, Any], **kw: Any) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return tool_error("text_to_speech requires 'text'")
    raw_voice = (args.get("voice") or "").strip()
    engine = (args.get("engine") or "auto").strip().lower()
    try:
        speed = float(args.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.5, min(2.0, speed))

    normalized = _normalize_text(text)
    if not normalized:
        return tool_error("text is empty after normalization")

    # 云端语音 id（如 `冰糖` / `mimo_voicedesign:...`）— Piper/pyttsx3 无法直接合成。
    # 若 fallback_to_local=True，自动回退到默认本地中文语音而非硬失败。
    is_cloud = bool(raw_voice and _is_cloud_voice(raw_voice))
    fallback_requested = bool(args.get("fallback_to_local") or args.get("fallback"))
    if is_cloud and not fallback_requested:
        return tool_error(
            f"voice {raw_voice!r} is a cloud-provider id, not a local Piper voice",
            hint=(
                "Set tts.engine=cloud in Desktop settings, pass fallback_to_local=True to auto-fallback, or omit the voice argument so the runner auto-picks a Chinese Piper voice for CJK text. See runner/README.md §音频工具."
            ),
            success=False,
        )

    # 用户级 voice id 是路由的唯一真实来源（见 runner/README §本地 TTS voice 选型）：
    # 显式 caller 偏好优先，否则按 "默认中文" 方向回退到内置 ZH 语音
    voice = pick_voice_for_text(preferred="" if is_cloud else raw_voice)

    dst = _output_path(name_hint="piper" if engine == "piper" else "tts")

    # piper_available() 只检查包是否导入 — 语音可能不在磁盘上，因此下方再校验，
    # 缺失则自动下载，再回退到 pyttsx3
    engine_chain: list[tuple[str, str]] = []  # (engine_name, voice_id)

    if engine == "piper":
        if not (piper_available() and _is_voice_installed(voice)):
            return tool_error(f"piper voice {voice!r} not installed under {piper_voice_dir()}", hint="Use `list_tts_voices` to see installed voices, or pass engine='pyttsx3'.")
        engine_chain.append(("piper", voice))
    elif engine == "pyttsx3":
        if not pyttsx3_available():
            return tool_error("pyttsx3 engine not installed", hint="`pyttsx3` should be in the runner wheel — re-install if missing.")
        engine_chain.append(("pyttsx3", ""))
    else:
        # Auto 模式：先尝试请求的 Piper voice；缺失时自动下载，让首次安装擦除 $SPIRITAGENT_HOME 后仍能在本地发声
        cfg_default = default_voice_id()
        piper_voice_for_chain: str | None = None
        for candidate in (voice, *([cfg_default] if voice != cfg_default else [])):
            if PIPER_VOICE_RE.match(candidate):
                # ensure_voice_installed 在语音已在磁盘上时短路返回 True；否则尝试下载
                if ensure_voice_installed(candidate):
                    piper_voice_for_chain = candidate
                    break
        if piper_voice_for_chain is not None:
            engine_chain.append(("piper", piper_voice_for_chain))
        if pyttsx3_available():
            engine_chain.append(("pyttsx3", ""))
        if not engine_chain:
            return tool_error(
                "no TTS engine available: piper voice not on disk and pyttsx3 not importable",
                hint=(f"Download a Piper voice to {piper_voice_dir()}, or install pyttsx3. Bundled voice ids: {', '.join(bundled_voices())}."),
            )

    # 显式 engine=piper/pyttsx3 尊重调用方选择 — 只有 auto 模式在失败（模型损坏、OOM、合成中途文件缺失）时才会静默落到下一个引擎
    is_auto = engine not in ("piper", "pyttsx3")
    last_error: Exception | None = None
    info: dict[str, Any] | None = None
    for eng_name, eng_voice in engine_chain:
        try:
            if eng_name == "piper":
                info = _synth_piper(normalized, eng_voice, speed, dst)
            else:
                # 链路中 pyttsx3 这一槽永远不携带 voice id — _synth_pyttsx3 在其 COM 作用域内对 CJK 文本自动挑中文 SAPI5 语音
                info = _synth_pyttsx3(normalized, None, speed, dst)
            break
        except Exception as e:
            last_error = e
            logger.warning("%s synthesis failed: %s", eng_name, e)
            if not is_auto:
                return tool_error(f"{eng_name} failed: {e}", success=False)

    if info is None:
        return _all_engines_failed(last_error)

    sz = Path(info["path"]).stat().st_size if Path(info["path"]).exists() else 0
    return tool_result(success=True, path=info["path"], engine=info["engine"], voice=info["voice"], size_bytes=sz, speed=speed)


def list_tts_voices_tool(args: dict[str, Any], **kw: Any) -> str:
    voices = list_installed_voices()
    return tool_result(success=True, voices=voices, count=len(voices), voices_dir=str(piper_voice_dir()), bundled=list(bundled_voices()))


registry.register_tool("text_to_speech", schema=TEXT_TO_SPEECH_SCHEMA, check_fn=_check_tts)(lambda args, **kw: text_to_speech_tool(args, **kw))
registry.register_tool("list_tts_voices", schema=LIST_VOICES_SCHEMA, check_fn=piper_available)(lambda args, **kw: list_tts_voices_tool(args, **kw))
