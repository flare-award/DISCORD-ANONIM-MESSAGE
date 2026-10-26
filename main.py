"""Точка входа для локального запуска Discord-бота анонимных сообщений."""

from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp
import discord

from bot import AnonBot
from config import BotConfig, ConfigError, load_config


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает стандартное логирование с выводом в stdout."""
    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def _build_socks_connector(proxy_url: str | None) -> aiohttp.BaseConnector | None:
    """Создаёт ProxyConnector для SOCKS4/SOCKS5-прокси внутри запущенного event loop."""
    if not proxy_url or not proxy_url.startswith(("socks5://", "socks4://")):
        return None

    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as exc:
        raise ConfigError(
            "Для подключения через SOCKS-прокси требуется пакет 'aiohttp-socks'. "
            "Выполните команду: pip install -r requirements.txt "
            "(либо укажите HTTP-прокси вида http://127.0.0.1:порт в PROXY_URL)."
        ) from exc

    return ProxyConnector.from_url(proxy_url, rdns=True, limit=0)


async def _run_bot(config: BotConfig) -> None:
    """Асинхронно инициализирует сетевой коннектор и запускает Discord-бота."""
    connector = _build_socks_connector(config.proxy_url)
    bot = AnonBot(config, connector=connector)
    async with bot:
        await bot.start(config.discord_token, reconnect=True)


def main() -> int:
    """Загружает конфигурацию, настраивает логирование и запускает бота."""
    setup_logging(logging.INFO)
    logger = logging.getLogger("anon_bot.startup")

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1

    logging.getLogger().setLevel(config.log_level_int)

    guild_display = (
        str(config.guild_id) if config.guild_id is not None else "GLOBAL (не задан)"
    )
    logger.info(
        "Инициализация бота | discord.py=%s | guild_id=%s | "
        "anon_channel_id=%s | public_channel_id=%s | log_level=%s | network=%s",
        discord.__version__,
        guild_display,
        config.anon_channel_id,
        config.public_channel_id,
        config.log_level,
        config.masked_proxy_display,
    )

    if config.anon_channel_id == config.public_channel_id:
        logger.warning(
            "ANON_CHANNEL_ID и PUBLIC_CHANNEL_ID совпадают (%s). "
            "Рекомендуется использовать отдельный канал для приёма команд и отдельный общий канал.",
            config.anon_channel_id,
        )

    try:
        asyncio.run(_run_bot(config))
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1
    except discord.LoginFailure:
        logger.exception(
            "Ошибка авторизации: недействительный DISCORD_TOKEN. "
            "Проверьте значение DISCORD_TOKEN в файле .env."
        )
        return 1
    except aiohttp.ClientProxyConnectionError:
        logger.exception(
            "Не удалось подключиться к прокси-серверу: %s. "
            "Проверьте, что ваш VPN/прокси-клиент запущен и порт указан верно в PROXY_URL "
            "(либо укажите PROXY_URL=direct в .env для прямого подключения).",
            config.masked_proxy_display,
        )
        return 1
    except (aiohttp.ClientConnectorError, TimeoutError, OSError):
        logger.exception(
            "Сетевая ошибка подключения к серверам Discord (discord.com:443) | режим: %s.\n"
            "Возможная причина: доступ к Discord заблокирован провайдером, а трафик Python "
            "идёт в обход вашего VPN/прокси.\n"
            "Как исправить:\n"
            "  1) Укажите локальный прокси вашего VPN-клиента в файле .env, например:\n"
            "     PROXY_URL=http://127.0.0.1:2080   или   PROXY_URL=socks5://127.0.0.1:10808\n"
            "  2) Либо включите в VPN-клиенте режим 'Системный прокси' (System Proxy) или 'TUN / VPN-режим' "
            "(и добавьте python.exe в список туннелируемых программ при раздельном туннелировании).\n"
            "  3) Если используете Zapret, запускайте профиль general (а не только discord), "
            "так как боту нужен доступ по HTTPS к discord.com:443 и gateway.discord.gg:443.",
            config.masked_proxy_display,
        )
        return 1
    except KeyboardInterrupt:
        logger.info("Остановка бота по сигналу пользователя (Ctrl+C).")
        return 0
    except discord.DiscordException:
        logger.exception("Критическая ошибка Discord при работе бота.")
        return 1
    except Exception:
        logger.exception("Непредвиденная критическая ошибка при запуске бота.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
