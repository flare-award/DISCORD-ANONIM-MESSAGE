"""Загрузка и валидация конфигурации Discord-бота из переменных окружения и файла .env."""

from __future__ import annotations

import logging
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

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

DIRECT_PROXY_VALUES: frozenset[str] = frozenset(
    {"direct", "none", "off", "false", "0"}
)

PROXY_SCHEME_MAP: dict[str, str] = {
    "http": "http",
    "https": "http",
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks": "socks5",
    "socks4": "socks4",
    "socks4a": "socks4",
}


class ConfigError(ValueError):
    """Исключение, выбрасываемое при некорректной или неполной конфигурации."""


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Проверенная конфигурация бота.

    Поля ``discord_token`` и ``proxy_url`` исключены из ``repr``, чтобы секреты
    и учётные данные прокси никогда случайно не попали в логи или отладочный вывод.
    """

    discord_token: str = field(repr=False)
    guild_id: int | None
    anon_channel_id: int
    public_channel_id: int
    log_level: str = "INFO"
    proxy_url: str | None = field(default=None, repr=False)
    proxy_source: str = "прямое подключение"

    @property
    def log_level_int(self) -> int:
        """Возвращает числовой уровень логирования для модуля ``logging``."""
        return int(getattr(logging, self.log_level))

    @property
    def masked_proxy_display(self) -> str:
        """Возвращает безопасное строковое описание режима подключения без паролей."""
        if not self.proxy_url:
            return self.proxy_source
        return f"{mask_proxy_url(self.proxy_url)} ({self.proxy_source})"


def mask_proxy_url(proxy_url: str) -> str:
    """Скрывает логин и пароль из URL прокси перед выводом в логи."""
    parsed = urlsplit(proxy_url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    host_port = f"{host}:{parsed.port}" if parsed.port else host
    if parsed.username is not None or parsed.password is not None:
        netloc = f"***@{host_port}"
    else:
        netloc = host_port
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def normalize_proxy_url(raw_url: str, *, var_name: str = "PROXY_URL") -> str:
    """Нормализует и проверяет URL прокси (HTTP, SOCKS5, SOCKS4)."""
    candidate = raw_url.strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    parsed = urlsplit(candidate)
    raw_scheme = parsed.scheme.lower()
    normalized_scheme = PROXY_SCHEME_MAP.get(raw_scheme)
    if normalized_scheme is None:
        raise ConfigError(
            f"Переменная {var_name} содержит неподдерживаемую схему {raw_scheme!r}. "
            "Используйте http://host:port, socks5://host:port или socks4://host:port."
        )

    host = parsed.hostname
    if not host:
        raise ConfigError(
            f"В переменной {var_name} не указан хост прокси "
            "(пример: http://127.0.0.1:2080 или socks5://127.0.0.1:10808)."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(
            f"В переменной {var_name} указан некорректный порт прокси."
        ) from exc

    if port is None or not (1 <= port <= 65535):
        raise ConfigError(
            f"В переменной {var_name} обязательно укажите порт от 1 до 65535 "
            "(например, http://127.0.0.1:2080)."
        )

    formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    auth_prefix = ""
    if parsed.username is not None:
        if parsed.password is not None:
            auth_prefix = f"{parsed.username}:{parsed.password}@"
        else:
            auth_prefix = f"{parsed.username}@"

    netloc = f"{auth_prefix}{formatted_host}:{port}"
    return urlunsplit((normalized_scheme, netloc, "", "", ""))


def _detect_system_proxy(environ: Mapping[str, str] | None) -> tuple[str | None, str]:
    """Определяет прокси из переменных окружения или системных настроек ОС (Windows/macOS/Linux)."""
    env_keys = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")
    source = environ if environ is not None else os.environ

    for key in env_keys:
        val = (source.get(key) or "").strip()
        if val:
            try:
                return normalize_proxy_url(val, var_name=key), f"из окружения {key}"
            except ConfigError:
                continue

    if environ is not None:
        return None, "прямое подключение"

    try:
        sys_proxies = urllib.request.getproxies()
    except Exception:
        return None, "прямое подключение"

    raw_socks = (sys_proxies.get("socks") or "").strip()
    raw_https = (sys_proxies.get("https") or "").strip()
    raw_http = (sys_proxies.get("http") or "").strip()
    raw_all = (sys_proxies.get("all") or "").strip()

    # В Windows реестр при socks=127.0.0.1:port подставляет socks4:// в https/http;
    # современные локальные клиенты (v2rayN, Nekoray, Clash и др.) работают по SOCKS5.
    chosen = raw_https or raw_http or raw_all or raw_socks
    if raw_socks and chosen.startswith("socks4://") and raw_socks.startswith("socks://"):
        chosen = "socks5://" + raw_socks.removeprefix("socks://")

    if not chosen:
        return None, "прямое подключение"

    try:
        return normalize_proxy_url(chosen, var_name="SYSTEM_PROXY"), "системный прокси ОС"
    except ConfigError:
        return None, "прямое подключение"


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

    proxy_url: str | None = None
    proxy_source: str = "прямое подключение"
    raw_proxy = (source.get("PROXY_URL") or "").strip()
    if raw_proxy:
        if raw_proxy.lower() in DIRECT_PROXY_VALUES:
            proxy_url = None
            proxy_source = "прямое подключение (PROXY_URL=direct)"
        else:
            try:
                proxy_url = normalize_proxy_url(raw_proxy, var_name="PROXY_URL")
                proxy_source = "из PROXY_URL"
            except ConfigError as exc:
                errors.append(str(exc))
    else:
        proxy_url, proxy_source = _detect_system_proxy(environ)

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
        proxy_url=proxy_url,
        proxy_source=proxy_source,
    )
