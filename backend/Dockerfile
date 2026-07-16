FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY runner/tools/ /runner/tools/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
