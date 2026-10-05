"""Level registry. Import levels here and register their mock policies."""

from __future__ import annotations

from ..core.level_base import Level
from .level1 import Level1
from .level2 import Level2
from .level3 import Level3
from .level4 import Level4
from .level5 import Level5

_LEVEL_CLASSES = [Level1, Level2, Level3, Level4, Level5]

LEVELS: dict[str, Level] = {}
for cls in _LEVEL_CLASSES:
    inst = cls()
    inst.register()  # binds mock policy
    LEVELS[inst.meta.id] = inst

ORDERED = sorted(LEVELS.values(), key=lambda l: l.meta.number)
