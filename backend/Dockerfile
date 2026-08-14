FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# APT: replace Debian sources with Tsinghua mirror
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; \
            s|security.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    blender \
    && rm -rf /var/lib/apt/lists/* \
    # numpy: Blender's glTF importer (used by inject_morph_targets.py) requires it;
    # without it the morph injection silently fails and models ship without morph targets.
    && pip install uv numpy

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY runner/tools/ /runner/tools/

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
