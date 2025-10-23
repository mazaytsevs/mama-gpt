from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from app.infra.health import healthz
from app.infra.metrics import Metrics
from app.infra.settings import BotMode, ParseMode, Settings
from app.llm.history import ConversationHistory
from app.llm.prompt import PromptManager

from .auth import is_admin


class CommandError(Exception):
    """Raised when command execution should stop with a message."""


@dataclass(slots=True)
class CommandContext:
    user_id: int
    chat_id: int
    request_id: str
    settings: Settings
    parse_mode: ParseMode
    prompt_manager: PromptManager
    history: ConversationHistory
    metrics: Metrics


@dataclass(slots=True)
class CommandResponse:
    text: str
    parse_mode: ParseMode | None = None
    store_in_history: bool = False


async def handle_command(name: str, args: Sequence[str], ctx: CommandContext) -> CommandResponse:
    name = name.lower()
    if name == "/start":
        return CommandResponse(
            text=(
                "Привет! Я дружелюбный помощник только для мамы и Маши. "
                "Задавай вопрос текстом, я постараюсь ответить быстро и по делу."
            ),
            parse_mode=ctx.parse_mode,
        )
    if name == "/help":
        return CommandResponse(
            text=(
                "Напиши вопрос обычным текстом. Можно уточнять или задавать новые вопросы. "
                "Если ответ не подходит — уточни, чего именно не хватает. "
                "Команды для Маши: /mode friendly|concise, /stats, /health."
            ),
            parse_mode=ctx.parse_mode,
        )
    if name == "/stats":
        _ensure_admin(ctx)
        return await _handle_stats(ctx)
    if name == "/mode":
        _ensure_admin(ctx)
        return await _handle_mode(args, ctx)
    if name == "/health":
        _ensure_admin(ctx)
        return await _handle_health(ctx)
    return CommandResponse(
        text="Не знаю такой команды. Напиши текстом, о чём хочешь спросить.",
        parse_mode=ctx.parse_mode,
    )


def _ensure_admin(ctx: CommandContext) -> None:
    if not is_admin(ctx.user_id, ctx.settings):
        raise CommandError("Эта команда доступна только Маше.")


async def _handle_mode(args: Sequence[str], ctx: CommandContext) -> CommandResponse:
    if not args:
        current_mode = ctx.prompt_manager.mode.value
        return CommandResponse(
            text=f"Сейчас режим: {current_mode}. Доступные варианты: friendly, concise.",
            parse_mode=ctx.parse_mode,
        )
    raw_mode = args[0].lower()
    try:
        mode = BotMode(raw_mode)
    except ValueError:
        raise CommandError("Используй /mode friendly или /mode concise.")
    await ctx.prompt_manager.set_mode(mode)
    return CommandResponse(
        text=f"Готово. Режим переключён на {mode.value}.",
        parse_mode=ctx.parse_mode,
    )


async def _handle_stats(ctx: CommandContext) -> CommandResponse:
    metrics = ctx.metrics
    if not metrics.enabled:
        return CommandResponse(
            text="Метрики выключены.",
            parse_mode=ctx.parse_mode,
        )

    summary_lines: List[str] = []
    for metric in metrics.registry.collect():
        if metric.name == "requests_total":
            total = sum(sample.value for sample in metric.samples)
            summary_lines.append(f"Всего запросов: {int(total)}")
        elif metric.name == "errors_total":
            total = sum(sample.value for sample in metric.samples)
            summary_lines.append(f"Ошибки: {int(total)}")
        elif metric.name == "latency_ms":
            count = 0
            total = 0.0
            for sample in metric.samples:
                if sample.name.endswith("_sum"):
                    total = sample.value
                elif sample.name.endswith("_count"):
                    count = sample.value
            avg = total / count if count else 0.0
            summary_lines.append(f"Средняя задержка: {avg:.0f} мс")

    if not summary_lines:
        summary_lines.append("Метрик пока нет.")

    text = "Статистика:\n" + "\n".join(f"- {line}" for line in summary_lines)
    return CommandResponse(text=text, parse_mode=ctx.parse_mode)


async def _handle_health(ctx: CommandContext) -> CommandResponse:
    redis_client = ctx.history.redis_client if ctx.history.enabled else None
    report = await healthz(redis_client)
    text = "Здоровье сервиса:\n"
    text += f"- Общее состояние: {report['status']}\n"
    redis_info = report.get("redis", {})
    text += f"- Redis: {redis_info.get('status')}"
    return CommandResponse(text=text, parse_mode=ctx.parse_mode)
