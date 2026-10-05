"""Capital use for a book that adds names each session and holds them for several days.

Each entry uses 1 / horizon of capital. That slice is split across top_n slots.
An empty slot earns zero. A new batch is not treated as the whole account.
"""

from datetime import date

from shortalpha.calendar import NYSECalendar


def slot_weight(top_n: int, horizon: int) -> float:
    if top_n < 1 or horizon < 1:
        raise ValueError("top_n and horizon must be positive")
    return 1.0 / (top_n * horizon)


def day_contribution(
    legs: list[tuple[float, float]],
    *,
    top_n: int,
    horizon: int,
    cost: float,
) -> tuple[float, float]:
    """Return capital-scaled net return and net excess for one entry session."""
    weight = slot_weight(top_n, horizon)
    net = 0.0
    excess = 0.0
    for stock_return, spy_return in legs[:top_n]:
        net += (stock_return - cost) * weight
        excess += (stock_return - cost - spy_return) * weight
    return net, excess


def contiguous_blocks(
    days: list[date],
    size: int,
    calendar: NYSECalendar | None,
) -> list[list[date]]:
    if size < 1:
        raise ValueError("block size must be positive")
    ordered = sorted(days)
    if not ordered:
        return []
    blocks: list[list[date]] = [[ordered[0]]]
    for day in ordered[1:]:
        previous = blocks[-1][-1]
        follows = calendar is not None and calendar.shift(previous, 1) == day
        if follows and len(blocks[-1]) < size:
            blocks[-1].append(day)
            continue
        blocks.append([day])
    return blocks
