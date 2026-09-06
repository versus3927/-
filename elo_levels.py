"""FACEIT-style CS2 Elo level thresholds adapted for DOMINION.

DOMINION uses 0 as the minimum Elo and starts Level 10 at exactly 2000 Elo.
"""

ELO_LEVELS = (
    (1, 0, 500),
    (2, 501, 750),
    (3, 751, 900),
    (4, 901, 1050),
    (5, 1051, 1200),
    (6, 1201, 1350),
    (7, 1351, 1530),
    (8, 1531, 1750),
    (9, 1751, 1999),
    (10, 2000, None),
)


def elo_level(points: int) -> int:
    value = max(0, int(points))
    for level, minimum, maximum in ELO_LEVELS:
        if maximum is None or minimum <= value <= maximum:
            return level
    return 1


def elo_bounds(points: int):
    """Return level, current floor, next-level floor and progress ratio."""
    value = max(0, int(points))
    level = elo_level(value)
    _, minimum, maximum = ELO_LEVELS[level - 1]
    if maximum is None:
        return level, minimum, None, 1.0
    next_minimum = maximum + 1
    span = max(1, next_minimum - minimum)
    progress = max(0.0, min(1.0, (value - minimum) / span))
    return level, minimum, next_minimum, progress


def elo_range_label(level: int) -> str:
    level = max(1, min(10, int(level)))
    _, minimum, maximum = ELO_LEVELS[level - 1]
    return f"{minimum}+" if maximum is None else f"{minimum}–{maximum}"


def elo_table_text() -> str:
    return " · ".join(f"LVL {level}: {elo_range_label(level)}" for level in range(1, 11))
