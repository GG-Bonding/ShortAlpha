"""Fixed linear maps. Breakpoints live in config and are not fit to returns."""


def scale_to_weight(raw: float, low: float, high: float, weight: float) -> float:
    if raw <= low:
        return 0.0
    if raw >= high:
        return weight
    return weight * (raw - low) / (high - low)


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
