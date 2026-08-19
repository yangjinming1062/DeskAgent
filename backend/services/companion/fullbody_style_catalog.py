from dataclasses import dataclass
from typing import Literal

FullbodyStyle = Literal["cel_shading", "anime_game_cg"]


@dataclass(frozen=True)
class FullbodyStyleInfo:
    id: str
    label_zh: str


STYLE_CATALOG: list[FullbodyStyleInfo] = [FullbodyStyleInfo(id="cel_shading", label_zh="日系赛璐珞"), FullbodyStyleInfo(id="anime_game_cg", label_zh="二次元游戏CG")]
