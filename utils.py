"""Мелкие помощники без внешних зависимостей: деление длинного текста, разбор чисел."""

from __future__ import annotations

MAX_MESSAGE = 3500  # Telegram режет сообщения длиннее 4096 символов, оставляем запас


def chunks(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Делит длинный текст по строкам на части, которые влезают в одно сообщение."""
    pieces: list[str] = []
    for line in text.split("\n"):
        while len(line) > limit:  # очень длинная строка режется на куски
            pieces.append(line[:limit])
            line = line[limit:]
        pieces.append(line)
    parts: list[str] = []
    current: str | None = None
    for piece in pieces:
        if current is None:
            current = piece
        elif len(current) + 1 + len(piece) > limit:
            parts.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}"
    if current is not None:
        parts.append(current)
    return [p for p in parts if p.strip()] or [""]


def parse_number(raw: str) -> float:
    return float(raw.replace(",", ".").replace(" ", "").replace("₽", ""))
