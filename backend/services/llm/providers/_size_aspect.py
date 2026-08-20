# OpenAI 像素尺寸 → Gemini/MiniMax 的 aspect_ratio；老 DALL·E 调用方仍传 size，此处统一翻译以保持图生供应商对工具层即插即用。
SIZE_TO_ASPECT: dict[str, str] = {"1024x1024": "1:1", "1024x1792": "9:16", "1792x1024": "16:9", "2048x2048": "1:1", "512x512": "1:1"}
