import time
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass
class UIElement:
    index: int
    role: str
    label: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    app: str = ""
    pid: int = 0
    window_id: int = 0
    element_token: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bounds
        return x + w // 2, y + h // 2


@dataclass
class CaptureResult:
    mode: str
    width: int
    height: int
    png_b64: str | None = None
    elements: list[UIElement] = field(default_factory=list)
    app: str = ""
    window_title: str = ""
    png_bytes_len: int = 0
    image_mime_type: str | None = None


@dataclass
class ActionResult:
    ok: bool
    action: str
    message: str = ""
    capture: CaptureResult | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Verdict surface (matches hermes-agent ``_classify_action_result``):
    #   ``verified``   — capture re-checked the action took effect.
    #   ``effect``     — short human label, e.g. "opened file", "no-op".
    #   ``escalation`` — ``"done"`` | ``"verify_fresh_state"`` | ``"escalate"``.
    #   ``path``       — which fallback ladder branch produced this result.
    #   ``code``       — numeric status, parallels a typed_error code.
    verified: bool = False
    effect: str = ""
    escalation: str = "done"
    path: str = ""
    code: int = 0
    # Background vs foreground escalation: backend implementations tag
    # which delivery channel the action went through so the runner
    # caller can apply per-channel approval scope. Defaults to
    # ``"background"`` to match the hermes-agent default.
    delivery_mode: str = "background"


# Sentinel values for `app=` that target the OS shell surface (desktop
# background / taskbar) rather than a specific application. Both backends
# resolve the sentinel to the topmost shell window — Finder / Dock on macOS,
# Progman / Shell_TrayWnd on Windows. Centralized here so the platform
# backends can't silently diverge on what counts as a sentinel.
DESKTOP_SENTINELS: frozenset[str] = frozenset({"screen", "desktop", "fullscreen", "all"})


class ComputerUseBackend(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult: ...

    @abstractmethod
    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def type_text(self, text: str) -> ActionResult: ...

    @abstractmethod
    def key(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def list_apps(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult: ...

    @abstractmethod
    def set_value(self, value: str, element: int | None = None) -> ActionResult: ...

    def wait(self, seconds: float) -> ActionResult:
        time.sleep(max(0.0, min(seconds, 30.0)))
        return ActionResult(ok=True, action="wait", message=f"waited {seconds:.2f}s")
