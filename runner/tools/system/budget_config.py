from dataclasses import dataclass, field

from ..registry import DEFAULT_MAX_RESULT_SIZE_CHARS, registry

PINNED_THRESHOLDS: dict[str, float] = {"read_file": float("inf")}
DEFAULT_TURN_BUDGET_CHARS = 200_000
DEFAULT_PREVIEW_SIZE_CHARS = 1_500


@dataclass(frozen=True)
class BudgetConfig:
    default_result_size: int = DEFAULT_MAX_RESULT_SIZE_CHARS
    turn_budget: int = DEFAULT_TURN_BUDGET_CHARS
    preview_size: int = DEFAULT_PREVIEW_SIZE_CHARS
    tool_overrides: dict[str, int] = field(default_factory=dict)

    def resolve_threshold(self, tool_name: str) -> int | float:
        return val if (val := PINNED_THRESHOLDS.get(tool_name, self.tool_overrides.get(tool_name))) is not None else registry.get_max_result_size(default=self.default_result_size)


DEFAULT_BUDGET = BudgetConfig()
