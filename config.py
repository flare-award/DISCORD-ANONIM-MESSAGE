"""Загрузка и валидация конфигурации Discord-бота из переменных окружения и файла .env."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)

PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    {
        "...",
        "your_discord_bot_token_here",
        "your_bot_token_here",
        "your_token_here",
        "paste_your_token_here",
        "<discord_token>",
    }
)


class ConfigError(ValueError):
    """Исключение, выбрасываемое при некорректной или неполной конфигурации."""


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Проверенная конфигурация бота.

    Поле ``discord_token`` исключено из ``repr``, чтобы токен никогда случайно
    не попал в логи или отладочный вывод.
    """

    discord_token: str = field(repr=False)
    guild_id: int | None
    anon_channel_id: int
    public_channel_id: int
    log_level: str = "INFO"

    @property
    def log_level_int(self) -> int:
        """Возвращает числовой уровень логирования для модуля ``logging``."""
        return int(getattr(logging, self.log_level))


def _parse_discord_id(
    raw_value: str | None,
    var_name: str,
    *,
    required: bool,
) -> tuple[int | None, str | None]:
    """Парсит и валидирует целочисленный ID сущности Discord (сервера или канала)."""
    cleaned = (raw_value or "").strip()
    if not cleaned:
        if required:
            return None, (
                f"Переменная {var_name} не задана. "
                f"Укажите числовой ID Discord в файле .env."
            )
        return None, None

    if not (cleaned.isascii() and cleaned.isdigit()):
        return None, (
            f"Переменная {var_name} имеет неверный формат ({cleaned!r}): "
            f"ожидается положительное целое число (ID из Discord)."
        )

    parsed_id = int(cleaned)
    if parsed_id <= 0:
        return None, (
            f"Переменная {var_name} должна быть положительным числом больше нуля."
        )

    return parsed_id, None


def load_config(
    env_file: str | Path | None = ".env",
    environ: Mapping[str, str] | None = None,
) -> BotConfig:
    """Загружает переменные окружения из ``.env`` и валидирует все параметры.

    Args:
        env_file: Путь к файлу ``.env`` (если ``None`` — загрузка файла пропускается).
        environ: Необязательный словарь окружения (удобно для изолированной проверки).

    Returns:
        Экземпляр ``BotConfig`` с проверенными настройками.

    Raises:
        ConfigError: Если отсутствуют обязательные переменные или их формат некорректен.
    """
    env_path: Path | None = Path(env_file) if env_file is not None else None
    if environ is None and env_path is not None:
        load_dotenv(dotenv_path=env_path, override=False)

    source: Mapping[str, str] = environ if environ is not None else os.environ
    errors: list[str] = []

    raw_token = (source.get("DISCORD_TOKEN") or "").strip()
    if not raw_token:
        errors.append(
            "Переменная DISCORD_TOKEN не задана. Укажите токен бота в файле .env."
        )
    elif raw_token.lower() in PLACEHOLDER_TOKENS or any(ch.isspace() for ch in raw_token):
        errors.append(
            "Переменная DISCORD_TOKEN содержит шаблонное или некорректное значение. "
            "Скопируйте реальный токен бота из Discord Developer Portal."
        )

    guild_id, guild_err = _parse_discord_id(
        source.get("GUILD_ID"),
        "GUILD_ID",
        required=False,
    )
    if guild_err:
        errors.append(guild_err)

    anon_channel_id, anon_err = _parse_discord_id(
        source.get("ANON_CHANNEL_ID"),
        "ANON_CHANNEL_ID",
        required=True,
    )
    if anon_err:
        errors.append(anon_err)

    public_channel_id, public_err = _parse_discord_id(
        source.get("PUBLIC_CHANNEL_ID"),
        "PUBLIC_CHANNEL_ID",
        required=True,
    )
    if public_err:
        errors.append(public_err)

    raw_log_level = (source.get("LOG_LEVEL") or "").strip().upper()
    if not raw_log_level:
        log_level = "INFO"
    elif raw_log_level == "WARN":
        log_level = "WARNING"
    elif raw_log_level in VALID_LOG_LEVELS:
        log_level = raw_log_level
    else:
        allowed = ", ".join(sorted(VALID_LOG_LEVELS))
        errors.append(
            f"Переменная LOG_LEVEL имеет недопустимое значение {raw_log_level!r}. "
            f"Допустимые значения: {allowed}."
        )
        log_level = "INFO"

    if errors:
        hint = ""
        if environ is None and env_path is not None and not env_path.is_file():
            hint = (
                f"\nПодсказка: файл {str(env_path)!r} не найден. "
                "Скопируйте '.env.example' в '.env' и заполните значения."
            )
        bullet_list = "\n".join(f"  - {err}" for err in errors)
        raise ConfigError(
            f"Ошибка конфигурации окружения:\n{bullet_list}{hint}"
        )

    assert anon_channel_id is not None
    assert public_channel_id is not None

    return BotConfig(
        discord_token=raw_token,
        guild_id=guild_id,
        anon_channel_id=anon_channel_id,
        public_channel_id=public_channel_id,
        log_level=log_level,
    )
