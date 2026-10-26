"""Реализация Discord-бота и slash-команды /anonim_message для анонимных сообщений."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig

logger = logging.getLogger("anon_bot")

MAX_MESSAGE_LENGTH: int = 1900
EMBED_TITLE: str = "Анонимное сообщение"
EMBED_COLOR: discord.Color = discord.Color(0x607D8B)
CONFIRMATION_MESSAGE: str = "Сообщение отправлено анонимно"


class AnonCommandError(Exception):
    """Базовое исключение ошибок при обработке команды /anonim_message."""


class EmptyMessageError(AnonCommandError):
    """Текст сообщения пустой или состоит только из пробельных символов."""


class MessageTooLongError(AnonCommandError):
    """Длина текста сообщения превышает установленный лимит."""


class PublicChannelNotFoundError(AnonCommandError):
    """Целевой публичный канал не найден в Discord."""


class InvalidChannelTypeError(AnonCommandError):
    """Целевой публичный канал имеет неподдерживаемый тип."""


def validate_message_text(text: str) -> str:
    """Проверяет текст сообщения на непустоту и соблюдение лимита длины (1900 символов).

    Ручная проверка гарантирует понятный русскоязычный ephemeral-ответ пользователю
    как при пустом тексте/пробелах, так и при превышении лимита длины.
    """
    if not text or not text.strip():
        raise EmptyMessageError(
            "Текст сообщения не должен быть пустым или состоять только из пробелов."
        )

    if len(text) > MAX_MESSAGE_LENGTH:
        raise MessageTooLongError(
            f"Длина сообщения ({len(text)} симв.) превышает лимит {MAX_MESSAGE_LENGTH} символов."
        )

    return text


def build_anonymous_embed(text: str) -> discord.Embed:
    """Формирует публичный Embed без каких-либо сведений об авторе и без footer/author."""
    return discord.Embed(
        title=EMBED_TITLE,
        description=text,
        color=EMBED_COLOR,
    )


def format_invocation_log(interaction: discord.Interaction, text: str) -> str:
    """Формирует строку аудита вызова команды для вывода в терминал."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    guild_id = interaction.guild.id if interaction.guild else interaction.guild_id
    guild_name = interaction.guild.name if interaction.guild else None

    channel_id = interaction.channel_id
    channel_name = getattr(interaction.channel, "name", None)

    user = interaction.user
    nickname = user.nick if isinstance(user, discord.Member) else None

    return (
        f"ANON | ts={now_iso} "
        f"| guild={guild_id} name={guild_name!r} "
        f"| user={user} username={user.name!r} id={user.id} "
        f"global_name={user.global_name!r} display_name={user.display_name!r} nick={nickname!r} "
        f"| anon_channel={channel_id} channel_name={channel_name!r} "
        f"| text={text!r}"
    )


async def resolve_public_channel(
    bot: commands.Bot,
    public_channel_id: int,
) -> discord.TextChannel | discord.Thread:
    """Находит публичный канал через кэш или HTTP API и проверяет его тип."""
    channel = bot.get_channel(public_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(public_channel_id)
        except discord.NotFound as exc:
            raise PublicChannelNotFoundError(
                f"Публичный канал с ID {public_channel_id} не найден."
            ) from exc

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        actual_type = type(channel).__name__
        raise InvalidChannelTypeError(
            f"Канал ID={public_channel_id} имеет неподдерживаемый тип {actual_type}; "
            "ожидается текстовый канал (TextChannel) или ветка (Thread)."
        )

    return channel


async def _safe_send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    """Отправляет пользователю ephemeral-сообщение через followup или response."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception(
            "Не удалось отправить ephemeral-ответ пользователю id=%s",
            interaction.user.id,
        )


def create_anonim_message_command(bot: AnonBot) -> app_commands.Command:
    """Создаёт и настраивает slash-команду /anonim_message."""

    @app_commands.command(
        name="anonim_message",
        description="Отправить анонимное сообщение в общий чат",
    )
    @app_commands.describe(text="Текст анонимного сообщения")
    @app_commands.guild_only()
    async def anonim_message(interaction: discord.Interaction, text: str) -> None:
        """Обработчик команды /anonim_message."""
        logger.info(format_invocation_log(interaction, text))

        try:
            # 1. Откладываем ответ в ephemeral-режиме (виден только автору команды).
            await interaction.response.defer(ephemeral=True, thinking=True)

            # 2. Проверяем, что команда вызвана на сервере и строго в ANON_CHANNEL_ID.
            if interaction.guild is None:
                logger.warning(
                    "Отклонён вызов /anonim_message вне гильдии: user_id=%s",
                    interaction.user.id,
                )
                await interaction.followup.send(
                    "Эту команду можно использовать только на сервере.",
                    ephemeral=True,
                )
                return

            if interaction.channel_id != bot.config.anon_channel_id:
                logger.warning(
                    "Отклонён вызов /anonim_message в неразрешённом канале: "
                    "channel_id=%s (разрешён только %s), user_id=%s",
                    interaction.channel_id,
                    bot.config.anon_channel_id,
                    interaction.user.id,
                )
                await interaction.followup.send(
                    f"Эту команду можно использовать только в специальном канале "
                    f"<#{bot.config.anon_channel_id}>.",
                    ephemeral=True,
                )
                return

            # Валидация текста сообщения (не пустой, не длиннее 1900 символов).
            validated_text = validate_message_text(text)

            # 3. Получаем и проверяем целевой публичный канал.
            public_channel = await resolve_public_channel(
                bot,
                bot.config.public_channel_id,
            )

            # 4. Отправляем анонимный Embed от имени бота без пингов и данных автора.
            embed = build_anonymous_embed(validated_text)
            sent_message = await public_channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            logger.info(
                "ANON_SENT | public_channel=%s | message_id=%s | text_length=%d",
                public_channel.id,
                sent_message.id,
                len(validated_text),
            )

            # 5. Отправляем скрытое подтверждение автору команды.
            await interaction.followup.send(
                CONFIRMATION_MESSAGE,
                ephemeral=True,
            )

        except EmptyMessageError:
            logger.exception(
                "Отклонено пустое сообщение от пользователя id=%s",
                interaction.user.id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка: текст сообщения не может быть пустым или состоять только из пробелов.",
            )
        except MessageTooLongError:
            logger.exception(
                "Отклонено слишком длинное сообщение (%d симв.) от пользователя id=%s",
                len(text),
                interaction.user.id,
            )
            await _safe_send_ephemeral(
                interaction,
                f"Ошибка: сообщение слишком длинное ({len(text)} симв.). "
                f"Максимально допустимая длина — {MAX_MESSAGE_LENGTH} символов.",
            )
        except PublicChannelNotFoundError:
            logger.exception(
                "Публичный канал ID=%s не найден при обработке команды от user_id=%s",
                bot.config.public_channel_id,
                interaction.user.id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка конфигурации: общий канал для публикации сообщений не найден. "
                "Пожалуйста, сообщите администратору сервера.",
            )
        except InvalidChannelTypeError:
            logger.exception(
                "Неверный тип публичного канала ID=%s при обработке команды от user_id=%s",
                bot.config.public_channel_id,
                interaction.user.id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка конфигурации: указанный общий канал не поддерживает отправку текстовых сообщений. "
                "Пожалуйста, сообщите администратору сервера.",
            )
        except discord.Forbidden:
            logger.exception(
                "Недостаточно прав у бота для работы с публичным каналом ID=%s",
                bot.config.public_channel_id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка доступа: у бота нет прав на просмотр или отправку Embed-сообщений "
                "в общий канал (необходимы права View Channels, Send Messages и Embed Links).",
            )
        except discord.NotFound:
            logger.exception(
                "Объект Discord (канал или взаимодействие) не найден: public_channel_id=%s",
                bot.config.public_channel_id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка: канал для отправки сообщений не найден. Обратитесь к администратору.",
            )
        except discord.HTTPException:
            logger.exception(
                "Ошибка Discord HTTP API при отправке анонимного сообщения в канал ID=%s",
                bot.config.public_channel_id,
            )
            await _safe_send_ephemeral(
                interaction,
                "Ошибка Discord API при отправке сообщения. Попробуйте повторить попытку чуть позже.",
            )
        except discord.DiscordException:
            logger.exception(
                "Ошибка библиотеки Discord при обработке команды /anonim_message"
            )
            await _safe_send_ephemeral(
                interaction,
                "Произошла ошибка Discord при обработке команды. Попробуйте позже.",
            )
        except Exception:
            logger.exception(
                "Непредвиденная ошибка при обработке команды /anonim_message"
            )
            await _safe_send_ephemeral(
                interaction,
                "Произошла внутренняя ошибка бота. Пожалуйста, обратитесь к администратору.",
            )

    return anonim_message


class AnonBot(commands.Bot):
    """Основной класс Discord-бота для отправки анонимных сообщений."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            tree_cls=app_commands.CommandTree,
        )
        self.config: BotConfig = config
        self._commands_synced: bool = False

        command = create_anonim_message_command(self)
        if self.config.guild_id is not None:
            self.tree.add_command(
                command,
                guild=discord.Object(id=self.config.guild_id),
            )
        else:
            self.tree.add_command(command)

        self.tree.on_error = self._on_tree_error

    async def _on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Глобальный обработчик ошибок дерева slash-команд."""
        logger.exception(
            "Ошибка CommandTree при вызове команды пользователем id=%s",
            interaction.user.id,
            exc_info=error,
        )
        if isinstance(error, app_commands.NoPrivateMessage):
            await _safe_send_ephemeral(
                interaction,
                "Эта команда доступна только на сервере и не работает в личных сообщениях.",
            )
        else:
            await _safe_send_ephemeral(
                interaction,
                "Не удалось выполнить команду. Проверьте введённые параметры и попробуйте снова.",
            )

    async def on_ready(self) -> None:
        """Вызывается при успешном подключении к Gateway Discord и синхронизирует команды."""
        bot_name = str(self.user) if self.user is not None else "Unknown"
        bot_id = self.user.id if self.user is not None else "Unknown"
        guild_display = (
            str(self.config.guild_id)
            if self.config.guild_id is not None
            else "GLOBAL (не задан)"
        )

        logger.info(
            "Бот онлайн | discord.py=%s | bot=%s (id=%s) | guild_id=%s | "
            "anon_channel_id=%s | public_channel_id=%s",
            discord.__version__,
            bot_name,
            bot_id,
            guild_display,
            self.config.anon_channel_id,
            self.config.public_channel_id,
        )

        if self._commands_synced:
            logger.debug("Slash-команды уже были синхронизированы ранее в этой сессии.")
            return

        try:
            if self.config.guild_id is not None:
                guild_obj = discord.Object(id=self.config.guild_id)
                synced = await self.tree.sync(guild=guild_obj)
                synced_names = ", ".join(f"/{cmd.name}" for cmd in synced) or "нет"
                logger.info(
                    "Синхронизация команд завершена (guild_id=%s): синхронизировано %d (%s)",
                    self.config.guild_id,
                    len(synced),
                    synced_names,
                )
            else:
                synced = await self.tree.sync()
                synced_names = ", ".join(f"/{cmd.name}" for cmd in synced) or "нет"
                logger.info(
                    "Глобальная синхронизация команд завершена: синхронизировано %d (%s)",
                    len(synced),
                    synced_names,
                )
            self._commands_synced = True
        except discord.DiscordException:
            logger.exception(
                "Не удалось синхронизировать slash-команды (guild_id=%s). "
                "Проверьте GUILD_ID и убедитесь, что бот добавлен на сервер со scope 'applications.commands'.",
                self.config.guild_id,
            )
