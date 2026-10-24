"""Пользователи бота. Владелец (OWNER_ID из .env) открывает доступ другим командой /adduser <id>.

У каждого пользователя свой список наблюдения, портфель, план, уровни и отчёты
(папка data/users/<id>/). Новости из каналов и лент общие.
"""

from __future__ import annotations

import config
from storage import read_json, write_json


def load_users() -> list[int]:
    data = read_json(config.USERS_FILE, [])
    return [int(u) for u in data if str(u).lstrip("-").isdigit()] if isinstance(data, list) else []


def all_users() -> list[int]:
    """Владелец первым, потом остальные без повторов."""
    result = [config.owner_id] if config.owner_id is not None else []
    for uid in load_users():
        if uid not in result:
            result.append(uid)
    return result


def is_allowed(user_id: int) -> bool:
    return user_id == config.owner_id or user_id in load_users()


def add_user(user_id: int) -> bool:
    users = load_users()
    if user_id in users or user_id == config.owner_id:
        return False
    write_json(config.USERS_FILE, users + [user_id])
    return True


def remove_user(user_id: int) -> bool:
    users = load_users()
    if user_id not in users:
        return False
    write_json(config.USERS_FILE, [u for u in users if u != user_id])
    return True


def manual_tag(user_id: int | None = None) -> str:
    """Метка «ваших материалов»: чтобы пользователи видели только свои."""
    uid = user_id if user_id is not None else config.current_user.get()
    return f"user:{uid if uid is not None else config.owner_id}"


def is_own_manual(tags: list[str]) -> bool:
    """Материал текущего пользователя. Старые материалы без метки считаются материалами владельца."""
    tag = manual_tag()
    user_tags = [t for t in tags if t.startswith("user:")]
    if not user_tags:
        return tag == f"user:{config.owner_id}"
    return tag in user_tags
