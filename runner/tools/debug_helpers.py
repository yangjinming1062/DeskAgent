import datetime
import json
import logging
import uuid
from typing import Any

from utils import cfg_get
from utils import get_zast_home
from utils import load_config

logger = logging.getLogger(__name__)


class DebugSession:
    def __init__(self, tool_name: str, *, env_var: str) -> None:
        self.tool_name = tool_name
        # ``env_var`` is the historical parameter name; the value now lives
        # in ``config["debug"][env_var.lower()]`` so the runner can be
        # configured without env-var injection.
        self.enabled = bool(cfg_get(load_config(), "debug", env_var.lower(), default=False))
        self.session_id = str(uuid.uuid4()) if self.enabled else ""
        self.log_dir = get_zast_home() / "logs"
        self._calls: list[dict[str, Any]] = []
        self._start_time = datetime.datetime.now().isoformat() if self.enabled else ""
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("%s debug mode enabled - Session ID: %s", tool_name, self.session_id)

    @property
    def active(self) -> bool:
        return self.enabled

    def log_call(self, call_name: str, call_data: dict[str, Any]) -> None:
        if self.enabled:
            self._calls.append({"timestamp": datetime.datetime.now().isoformat(), "tool_name": call_name} | call_data)

    def save(self) -> None:
        if not self.enabled:
            return
        try:
            filepath = self.log_dir / f"{self.tool_name}_debug_{self.session_id}.json"
            payload = {
                "session_id": self.session_id,
                "start_time": self._start_time,
                "end_time": datetime.datetime.now().isoformat(),
                "debug_enabled": True,
                "total_calls": len(self._calls),
                "tool_calls": self._calls,
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.debug("%s debug log saved: %s", self.tool_name, filepath)
        except Exception as e:
            logger.error("Error saving %s debug log: %s", self.tool_name, e)

    def get_session_info(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "session_id": self.session_id or None,
            "log_path": str(self.log_dir / f"{self.tool_name}_debug_{self.session_id}.json") if self.enabled else None,
            "total_calls": len(self._calls),
        }
