"""Точка входа для локального запуска Discord-бота анонимных сообщений."""

from __future__ import annotations

import logging
import sys

import discord

from bot import AnonBot
from config import ConfigError, load_config


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
        "anon_channel_id=%s | public_channel_id=%s | log_level=%s",
        discord.__version__,
        guild_display,
        config.anon_channel_id,
        config.public_channel_id,
        config.log_level,
    )

    if config.anon_channel_id == config.public_channel_id:
        logger.warning(
            "ANON_CHANNEL_ID и PUBLIC_CHANNEL_ID совпадают (%s). "
            "Рекомендуется использовать отдельный канал для приёма команд и отдельный общий канал.",
            config.anon_channel_id,
        )

    bot = AnonBot(config)

    try:
        bot.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        logger.exception(
            "Ошибка авторизации: недействительный DISCORD_TOKEN. "
            "Проверьте значение DISCORD_TOKEN в файле .env."
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
