FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY runner/tools/ /runner/tools/


# Phase 4 — Blender headless for morph-target injection into rigged GLBs.
# Matches the team's local install (Blender Foundation, build hash fbe6228777e7).
# The morph script (``backend/assets/animations/inject_morph_targets.py``)
# targets the bpy API surface that ships with this build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    blender \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
