FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# APT: replace Debian sources with Tsinghua mirror
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; \
            s|security.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    xz-utils \
    # Blender 5.2 official tarball runtime deps (X11/GL/OpenMP) for headless
    # Cycles CPU rendering + glTF import. The tarball bundles its own Python
    # (3.13) and numpy, so the glTF importer no longer needs system numpy.
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxi6 \
    libxfixes3 \
    libxkbcommon0 \
    libxxf86vm1 \
    libxrandr2 \
    libgl1 \
    libegl1 \
    libsm6 \
    libice6 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install uv

# Install Blender 5.2.0 LTS from the official tarball. apt ships Blender 4.x,
# but the garment/body bpy pipelines must match the locally-used 5.2 to avoid
# cross-version API drift (e.g. cloth pin semantics).
ARG BLENDER_VERSION=5.2.0
RUN curl -fsSL -o /tmp/blender.tar.xz \
    "https://mirrors.tuna.tsinghua.edu.cn/blender/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    echo "96f6c181a30f4950607839dc84d42a354b250d8a0231b098b59b7bc69c351c48  /tmp/blender.tar.xz" | sha256sum -c - && \
    tar -xJf /tmp/blender.tar.xz -C /opt && \
    ln -sf "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" /usr/local/bin/blender && \
    rm /tmp/blender.tar.xz

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY runner/tools/ /runner/tools/

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
