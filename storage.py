"""Чтение и запись JSON-файлов в папке data.

Запись идёт через временный файл: если компьютер выключится посреди записи,
старый файл останется целым. Битый файл не затирается молча, а сохраняется рядом с пометкой .broken.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("stock-bot")


def read_json(path: Path, default: Any) -> Any:
    """Содержимое файла или default, если файла нет или он испорчен."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # utf-8-sig переживает BOM из Блокнота
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        backup = path.with_name(f"{path.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            path.replace(backup)
            log.error("Файл %s испорчен (%s). Сохранил копию: %s", path, exc, backup.name)
        except OSError:
            log.error("Файл %s испорчен (%s)", path, exc)
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):  # в Windows файл иногда ненадолго держит антивирус
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2)
