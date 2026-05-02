#!/usr/bin/env python3
"""Telegram -> Claude Code bridge with per-chat session isolation."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

load_dotenv(Path(__file__).parent / '.env')

TELEGRAM_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
ALLOWED_USER_IDS = {int(x) for x in os.environ.get('ALLOWED_USER_IDS', '').split(',') if x.strip()}
CLAUDE_CWD = os.path.expanduser(os.environ.get('CLAUDE_CWD', '~'))
CLAUDE_BIN = os.environ.get('CLAUDE_BIN', '/home/alex/.local/bin/claude')
IDLE_TIMEOUT = int(os.environ.get('IDLE_TIMEOUT_SECONDS', '1800'))
MAX_SESSIONS = int(os.environ.get('MAX_SESSIONS', '5'))

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s - %(message)s')
log = logging.getLogger('claude-tg-bot')


class ClaudeSession:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self.session_id: Optional[str] = None
        self.last_active: float = time.time()
        self._result_event: Optional[asyncio.Event] = None
        self._result_text: Optional[str] = None
        self._result_error: Optional[str] = None
        self._assistant_buffer: list[str] = []

    async def ensure_started(self):
        if self.proc is None or self.proc.returncode is not None:
            await self._start()

    async def _start(self):
        log.info('[chat=%s] starting claude subprocess', self.chat_id)
        self.proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, '-p',
            '--input-format=stream-json',
            '--output-format=stream-json',
            '--verbose',
            '--dangerously-skip-permissions',
            '--permission-mode=bypassPermissions',
            '--append-system-prompt',
            (
                '你通过 Telegram 与用户对话。行为准则：'
                '1) 收到请求就直接执行，不要向用户请求确认（用户无法在 Telegram 里点按钮）；'
                '2) 不要给用户多选项让他选择，你自己来判断并决定；'
                '3) 遇到歧义时，做最合理的选择并继续，然后在回复里简要说明你做了什么；'
                '4) 回复尽量简洁，避免长列表与复杂 Markdown。'
            ),
            cwd=CLAUDE_CWD,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def _read_stdout(self):
        assert self.proc and self.proc.stdout
        async for raw in self.proc.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                log.warning('[chat=%s] non-JSON: %r', self.chat_id, raw[:200])
                continue
            await self._handle_event(event)
        log.warning('[chat=%s] claude stdout ended (rc=%s)',
                    self.chat_id, self.proc.returncode if self.proc else 'n/a')
        if self._result_event is not None and not self._result_event.is_set():
            self._result_error = 'Claude process exited unexpectedly.'
            self._result_event.set()

    async def _read_stderr(self):
        assert self.proc and self.proc.stderr
        async for raw in self.proc.stderr:
            log.warning('[chat=%s] stderr: %s',
                        self.chat_id, raw.decode(errors='replace').rstrip())

    async def _handle_event(self, event: dict):
        t = event.get('type')
        if t == 'system' and event.get('subtype') == 'init':
            self.session_id = event.get('session_id')
            log.info('[chat=%s] session %s', self.chat_id, self.session_id)
        elif t == 'assistant':
            content = event.get('message', {}).get('content', []) or []
            for block in content:
                if block.get('type') == 'text':
                    text = block.get('text', '')
                    if text:
                        self._assistant_buffer.append(text)
        elif t == 'result':
            sub = event.get('subtype', '')
            fallback = ''.join(self._assistant_buffer).strip()
            if sub == 'success':
                main = (event.get('result') or '').strip()
                self._result_text = main if main else fallback
                self._result_error = None
            else:
                if fallback:
                    self._result_text = fallback
                    self._result_error = None
                else:
                    self._result_error = event.get('result') or f'claude: {sub}'
            if self._result_event is not None:
                self._result_event.set()

    async def send(self, text: str) -> str:
        await self.ensure_started()
        async with self.lock:
            self.last_active = time.time()
            self._result_event = asyncio.Event()
            self._result_text = None
            self._result_error = None
            self._assistant_buffer = []
            msg = {'type': 'user', 'message': {'role': 'user', 'content': text}}
            assert self.proc and self.proc.stdin
            self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8'))
            await self.proc.stdin.drain()
            await self._result_event.wait()
            if self._result_error:
                raise RuntimeError(self._result_error)
            return self._result_text or ''

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            log.info('[chat=%s] stopping claude', self.chat_id)
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


class SessionManager:
    def __init__(self):
        self.sessions: dict[int, ClaudeSession] = {}
        self.manager_lock = asyncio.Lock()

    async def get(self, chat_id: int) -> ClaudeSession:
        async with self.manager_lock:
            if chat_id not in self.sessions:
                if len(self.sessions) >= MAX_SESSIONS:
                    oldest = min(self.sessions.values(), key=lambda s: s.last_active)
                    log.info('capacity reached; evicting chat=%s (idle %.0fs)',
                             oldest.chat_id, time.time() - oldest.last_active)
                    await oldest.stop()
                    del self.sessions[oldest.chat_id]
                self.sessions[chat_id] = ClaudeSession(chat_id)
            return self.sessions[chat_id]

    async def reset(self, chat_id: int):
        async with self.manager_lock:
            sess = self.sessions.pop(chat_id, None)
        if sess:
            await sess.stop()

    async def idle_reaper(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            to_stop = []
            async with self.manager_lock:
                for chat_id, sess in list(self.sessions.items()):
                    if now - sess.last_active > IDLE_TIMEOUT:
                        to_stop.append(self.sessions.pop(chat_id))
            for sess in to_stop:
                log.info('reaping idle chat=%s', sess.chat_id)
                await sess.stop()


manager = SessionManager()


def is_allowed(update: Update) -> bool:
    u = update.effective_user
    return u is not None and u.id in ALLOWED_USER_IDS


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else '?'
    if not is_allowed(update):
        await update.message.reply_text(f'unauthorized (your id: {uid})')
        return
    chat_id = update.effective_chat.id
    sess = manager.sessions.get(chat_id)
    sid = sess.session_id if sess else '(none yet)'
    await update.message.reply_text(
        f'connected.\nchat_id={chat_id}\nsession={sid}\n'
        '/newsession to reset this chat'
    )


async def cmd_newsession(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text('restarting claude for this chat...')
    await manager.reset(chat_id)
    await update.message.reply_text('done.')


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = (update.message.text or '').strip()
    if not text:
        return
    chat_id = update.effective_chat.id
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception:
        pass
    try:
        sess = await manager.get(chat_id)
        reply = await sess.send(text)
    except Exception as e:
        log.exception('send failed')
        reply = f'error: {e}'
    if not reply:
        reply = '(no text in claude reply)'
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i + 4000])


async def post_init(app: Application):
    asyncio.create_task(manager.idle_reaper())


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('newsession', cmd_newsession))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info('polling...')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
