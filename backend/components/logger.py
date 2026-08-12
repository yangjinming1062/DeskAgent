import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from .config import SETTINGS

# stdlib LogRecord attributes — derived at import time so future Python
# releases that add new fields don't silently leak into JSON output.
# `color_message` is uvicorn's; `message`/`asctime` are populated by
# Formatter.format() and can collide with `extra=` keys.
_RESERVED_LOGRECORD_KEYS: frozenset[str] = frozenset(set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "color_message"})

# Caller 在 HTTP 入口 (services.correlation.correlation_id_middleware) 或
# 长生命周期 task tick 顶部 (services.cron.scheduler_loop / services.gateway.connection.
# _process_events) 调 set_request_id(...) 注入; _RequestContextFilter
# 自动透传到每条 LogRecord.
_user_id_var: ContextVar[int | None] = ContextVar("logger_user_id", default=None)
_request_id_var: ContextVar[str | None] = ContextVar("logger_request_id", default=None)


def set_request_user_id(user_id: int | None) -> None:
    _user_id_var.set(user_id)


def set_request_id(request_id: str | None) -> None:
    _request_id_var.set(request_id)


def current_request_id() -> str | None:
    """Public reader — prefer over touching `_request_id_var` directly."""
    return _request_id_var.get()


def _extras(record: logging.LogRecord) -> dict[str, object]:
    """non-reserved user extras + ContextVar-injected fields."""
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_LOGRECORD_KEYS and not k.startswith("_")}


class _RequestContextFilter(logging.Filter):
    """把 ContextVar 注入到每条 LogRecord. caller 显式 extra= 优先."""

    def filter(self, record: logging.LogRecord) -> bool:
        uid = _user_id_var.get()
        if uid is not None and "user_id" not in record.__dict__:
            record.user_id = uid
        rid = _request_id_var.get()
        if rid is not None and "request_id" not in record.__dict__:
            record.request_id = rid
        return True


class _JsonFormatter(logging.Formatter):
    """JSON 行输出. 不做脱敏 — 信任上游已脱敏的字符串."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "msg": msg,
                **_extras(record),
            },
            ensure_ascii=False,
            default=str,
        )


class _TextFormatter(logging.Formatter):
    """人类可读 fallback (dev 模式 env=LOG_FORMAT=text). extras 追加在尾部."""

    DEFAULT_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.DEFAULT_FMT, datefmt="%Y-%m-%dT%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        extras = _extras(record)
        if extras:
            msg += f" {extras}"
        return msg


_FORMATTERS: dict[str, type[logging.Formatter]] = {"json": _JsonFormatter, "text": _TextFormatter}


def setup_logging() -> None:
    """Lifespan 第一行调一次. 接管 root logger.

    不用 dictConfig — dictConfig 解析 handler dict 时会重新实例化,
    丢弃我们手动挂的 formatter/filter.
    """
    try:
        formatter_cls = _FORMATTERS[SETTINGS.log_format]
    except KeyError:
        # pydantic Literal 已拒; 这里只对 test monkey-patch 路径生效
        raise ValueError(f"invalid log_format: {SETTINGS.log_format!r}") from None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter_cls())
    handler.addFilter(_RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(SETTINGS.log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def get_logger(name: str | None = None) -> logging.Logger:
    """统一 logger 入口. None 走 logging.getLogger() 拿真正的 root."""
    return logging.getLogger(name) if name is not None else logging.getLogger()
