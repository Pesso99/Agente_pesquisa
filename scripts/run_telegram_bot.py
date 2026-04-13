from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.io_utils import stamp_for_id
from app.orchestrator import run_autonomous_cycle

logger = logging.getLogger(__name__)
load_dotenv(ROOT / ".env")


@dataclass
class BotState:
    running: bool = False
    current_job_id: str | None = None
    last_job_id: str | None = None
    last_html_path: Path | None = None
    last_error: str | None = None


STATE = BotState()
RUN_LOCK = asyncio.Lock()


def _allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            out.add(int(value))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ALLOWED_CHAT_IDS value: %s", value)
    return out


def _is_authorized(chat_id: int) -> bool:
    allowlist = _allowed_chat_ids()
    if not allowlist:
        return True
    return chat_id in allowlist


def _summary_path(job_id: str) -> Path:
    return ROOT / "data" / "jobs" / f"{job_id}_summary.json"


def _read_job_summary(job_id: str) -> dict[str, Any] | None:
    path = _summary_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: PERF203
        logger.warning("Failed to read summary for %s: %s", job_id, exc)
        return None


def _build_status_text() -> str:
    if STATE.running and STATE.current_job_id:
        return (
            f"<b>Status:</b> executando\n"
            f"<b>Job atual:</b> <code>{STATE.current_job_id}</code>"
        )
    if STATE.last_job_id:
        return (
            f"<b>Status:</b> ocioso\n"
            f"<b>Ultimo job:</b> <code>{STATE.last_job_id}</code>\n"
            f"<b>Ultimo erro:</b> {STATE.last_error or 'nenhum'}"
        )
    return "<b>Status:</b> ocioso\nNenhum job executado ainda."


async def _send_html_report(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    html_path: Path,
    caption: str,
) -> None:
    if not update.effective_chat:
        return
    if not html_path.exists():
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Relatorio HTML nao encontrado no caminho esperado.",
        )
        return
    with html_path.open("rb") as f:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=f,
            filename=html_path.name,
            caption=caption[:1024],
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    if not _is_authorized(update.effective_chat.id):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Chat nao autorizado.\n"
                f"Seu chat_id: {chat_id}\n"
                "Configure TELEGRAM_ALLOWED_CHAT_IDS no .env com o ID deste chat."
            ),
        )
        return

    allowlist = _allowed_chat_ids()
    mode = "restrito" if allowlist else "aberto (sem whitelist)"
    text = (
        "Bot conectado.\n\n"
        f"chat_id: <code>{chat_id}</code>\n"
        f"modo: <b>{mode}</b>\n\n"
        "Comandos disponiveis:\n"
        "/run - roda um ciclo autonomo e envia o HTML\n"
        "/status - mostra status da execucao\n"
        "/last - reenvia o ultimo HTML gerado"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not _is_authorized(update.effective_chat.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Chat nao autorizado.")
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_build_status_text(),
        parse_mode=ParseMode.HTML,
    )


async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not _is_authorized(update.effective_chat.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Chat nao autorizado.")
        return

    if not STATE.last_job_id or not STATE.last_html_path:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Nenhum relatorio disponivel ainda.",
        )
        return

    await _send_html_report(
        update=update,
        context=context,
        html_path=STATE.last_html_path,
        caption=f"Ultimo relatorio do job {STATE.last_job_id}",
    )


def _run_cycle_blocking(job_id: str) -> tuple[Path, dict[str, Any] | None]:
    result = run_autonomous_cycle(job_id, autonomous=True)
    html_path = result.report_paths["html"]
    summary = _read_job_summary(job_id)
    return html_path, summary


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not _is_authorized(update.effective_chat.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Chat nao autorizado.")
        return

    if RUN_LOCK.locked() or STATE.running:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Ja existe um job em execucao: {STATE.current_job_id or 'desconhecido'}",
        )
        return

    job_id = f"tg_{stamp_for_id()}"
    async with RUN_LOCK:
        STATE.running = True
        STATE.current_job_id = job_id
        STATE.last_error = None

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Iniciando ciclo autonomo...\nJob: <code>{job_id}</code>",
            parse_mode=ParseMode.HTML,
        )

        try:
            html_path, summary = await asyncio.to_thread(_run_cycle_blocking, job_id)
            STATE.last_job_id = job_id
            STATE.last_html_path = html_path

            counts = (summary or {}).get("counts", {})
            msg = (
                f"<b>Ciclo concluido</b>\n"
                f"<b>Job:</b> <code>{job_id}</code>\n"
                f"<b>Candidates:</b> {counts.get('candidates', 'n/a')}\n"
                f"<b>Observations:</b> {counts.get('observations', 'n/a')}\n"
                f"<b>Campaigns finais:</b> {counts.get('campaigns_after_dedupe', 'n/a')}"
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            await _send_html_report(
                update=update,
                context=context,
                html_path=html_path,
                caption=f"Relatorio HTML - {job_id}",
            )
        except Exception as exc:  # noqa: PERF203
            logger.exception("Failed to execute job %s", job_id)
            STATE.last_error = f"{type(exc).__name__}: {exc}"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"Falha ao executar o ciclo.\n"
                    f"Job: <code>{job_id}</code>\n"
                    f"Erro: <code>{type(exc).__name__}: {exc}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
        finally:
            STATE.running = False
            STATE.current_job_id = None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nao definido no .env")

    if not _allowed_chat_ids():
        logger.warning(
            "TELEGRAM_ALLOWED_CHAT_IDS nao definido. "
            "Bot iniciara em modo aberto (aceita qualquer chat)."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("last", last_cmd))

    logger.info("Telegram bot iniciado.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
