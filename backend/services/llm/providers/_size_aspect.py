# OpenAI-style pixel sizes → Gemini/MiniMax aspect_ratio strings. The legacy
# DALL·E schema callers still pass `size`; we translate so image-gen providers
# stay a drop-in for the tool layer.
SIZE_TO_ASPECT: dict[str, str] = {
    "1024x1024": "1:1",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "2048x2048": "1:1",
    "512x512": "1:1",
}
