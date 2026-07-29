from typing import Any


def to_provider_content(parts: list[Any]) -> list[dict]:
    """Normalize a chat ``content`` array to provider-neutral form.

    Accepts a mix of:
      - ``str`` (treated as a text part)
      - ``{"type": "text", "text": "..."}``
      - ``{"type": "image_url", "image_url": {"url": "...", ...}}``
      - ``{"type": "video_url", "video_url": {"url": "...", ...}}``

    Returns a list of typed parts the underlying SDK can serialize. Provider
    classes decide which parts to drop (e.g. some providers reject video_url).
    """
    out: list[dict] = []
    for p in parts:
        if isinstance(p, str):
            out.append({"type": "text", "text": p})
            continue
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if t == "text" and "text" in p:
            out.append({"type": "text", "text": p["text"]})
        elif t == "image_url" and isinstance(p.get("image_url"), dict):
            out.append({"type": "image_url", "image_url": p["image_url"]})
        elif t == "video_url" and isinstance(p.get("video_url"), dict):
            out.append({"type": "video_url", "video_url": p["video_url"]})
        elif t in ("input_audio", "audio_url") and p.get(t):
            out.append({"type": t, t: p[t]})
    return out