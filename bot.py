"""
🎵 Telegram Voice Chat Music Bot
"""

import os
import random
import asyncio
import logging
import nest_asyncio
nest_asyncio.apply()
from pytgcalls import idle, filters
from collections import deque

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from pytgcalls import PyTgCalls
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.audio_parameters import AudioParameters
from pyrogram import Client
import yt_dlp

# ═══════════════════════════════════════════════
#  CONFIG  (set these as environment variables — never hardcode secrets!)
# ═══════════════════════════════════════════════
API_ID         = int(os.environ.get("API_ID", "20764054"))
API_HASH       = os.environ.get("API_HASH", "e4471e35e9ca0f781d70d4d0920f75c1")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8800384691:AAHO9cRVADcPv73PfgpnOJrXyEXQt2Psxpg")
SESSION_STRING = os.environ.get("SESSION", "BQE81ZYAGqjo14uOxdeHiBPQRiCc4yNy6qHE9ml1wYrgVJnJTkjYmKyYLkLGD5KxnhMAgwsfTAUsfADZ6xvMiKa6MlzCmaxQMsyk0SYCiHf4IDjecle7yW-fGWrG54D4wZ8Yb_p3pOyQCuvVj13KFLkck-5kB4Pr4vlH9nCpxw6X8LHXT-blY--xORgbDrXTn-sXVLfrgGjbR1mbvpd4o61tfSLJPpYFXOIm1hF3eyIvs21xFhlWajLQI8iB5GnQPeupp4OjGkolZsZiod789De9NuYs2QVDMGlNM71Zlz_WfBbgGUt-4Ig1qtmLsy3ui4Plwo6nfT3s7iLixXUFqlPU_Zt7gwAAAAINlPQsAA")
DOWNLOAD_DIR   = "downloads"

AUDIO_QUALITY = AudioParameters.from_quality(os.environ.get("AUDIO_QUALITY", "MEDIUM").lower())

# ═══════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

queues: dict      = {}
now_playing: dict = {}
paused: dict      = {}
loop_mode: dict   = {}   # chat_id -> int (0 = off, N = repeat N times, -1 = infinite)
seek_pos: dict    = {}   # chat_id -> seconds played so far
tg_app = None

pyro_app = Client(
    name="music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)
calls = PyTgCalls(pyro_app)

# ─────────────────────────────────────────────
#  YOUTUBE DOWNLOAD
# ─────────────────────────────────────────────
def yt_download(query: str) -> dict:
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if "entries" in info:
                info = info["entries"][0]
            filepath = os.path.join(DOWNLOAD_DIR, f"{info['id']}.mp3")
            return {
                "success":   True,
                "filepath":  filepath,
                "title":     info.get("title", "Unknown"),
                "duration":  info.get("duration", 0),
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

def cleanup(filepath: str):
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass

def make_stream(filepath: str):
    return AudioPiped(filepath, audio_parameters=AUDIO_QUALITY)

def fmt_time(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"

# ─────────────────────────────────────────────
#  PROGRESS BAR  (zip bot style)
# ─────────────────────────────────────────────
def progress_bar(played: int, total: int, length: int = 10) -> str:
    if total <= 0:
        return "▱" * length
    filled = int(length * min(played, total) / total)
    return "▰" * filled + "▱" * (length - filled)

# ─────────────────────────────────────────────
#  PLAYER KEYBOARD  (enhanced zip-bot style UI)
# ─────────────────────────────────────────────
def player_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    is_paused  = paused.get(chat_id, False)
    loop_val   = loop_mode.get(chat_id, 0)
    loop_label = f"🔁 Loop: {'∞' if loop_val == -1 else loop_val if loop_val > 0 else 'Off'}"
    pause_btn  = InlineKeyboardButton(
        "▶️ Resume" if is_paused else "⏸️ Pause",
        callback_data=f"pause:{chat_id}"
    )
    return InlineKeyboardMarkup([
        # Row 1 — seek
        [
            InlineKeyboardButton("⏪ 10s",    callback_data=f"seek_back:{chat_id}"),
            InlineKeyboardButton("⏩ 10s",    callback_data=f"seek_fwd:{chat_id}"),
        ],
        # Row 2 — playback controls
        [
            pause_btn,
            InlineKeyboardButton("🔁 Replay", callback_data=f"replay:{chat_id}"),
            InlineKeyboardButton("⏭️ Skip",   callback_data=f"skip:{chat_id}"),
        ],
        # Row 3 — extra controls
        [
            InlineKeyboardButton(loop_label,          callback_data=f"loop:{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle",        callback_data=f"shuffle:{chat_id}"),
            InlineKeyboardButton("📋 Queue",           callback_data=f"show_queue:{chat_id}"),
        ],
        # Row 4 — stop
        [InlineKeyboardButton("⏹️ Stop & Close",     callback_data=f"stop:{chat_id}")],
    ])

# ─────────────────────────────────────────────
#  NOW PLAYING MESSAGE  (with progress bar)
# ─────────────────────────────────────────────
async def send_now_playing(chat_id: int, track: dict):
    played = seek_pos.get(chat_id, 0)
    total  = track.get("duration", 0)
    bar    = progress_bar(played, total)
    q_cnt  = len(queues.get(chat_id, []))

    text = (
        f"🎵 *Ab Chal Raha Hai*\n\n"
        f"🎶 *{track['title']}*\n"
        f"👤 *Requested by:* {track.get('requested_by', 'Unknown')}\n"
        f"⏱️ `{fmt_time(played)}` {bar} `{fmt_time(total)}`"
    )
    if q_cnt:
        text += f"\n📋 Queue mein aur *{q_cnt}* track(s)"

    await tg_app.bot.send_message(
        chat_id, text, parse_mode="Markdown",
        reply_markup=player_keyboard(chat_id)
    )

# ─────────────────────────────────────────────
#  PLAYBACK
# ─────────────────────────────────────────────
async def play_next(chat_id: int):
    # Loop mode check
    lv = loop_mode.get(chat_id, 0)
    if lv != 0 and chat_id in now_playing:
        track = now_playing[chat_id]
        if lv > 0:
            loop_mode[chat_id] = lv - 1
        seek_pos[chat_id] = 0
        paused[chat_id]   = False
        try:
            await calls.play(chat_id, make_stream(track["filepath"]))
            await send_now_playing(chat_id, track)
        except Exception as e:
            logger.exception("loop replay failed")
            await tg_app.bot.send_message(chat_id, f"❌ Loop error: {e}")
        return

    q = queues.get(chat_id)
    if not q:
        now_playing.pop(chat_id, None)
        paused.pop(chat_id, None)
        seek_pos.pop(chat_id, None)
        loop_mode.pop(chat_id, None)
        await tg_app.bot.send_message(chat_id, "✅ Queue khatam! Aur songs ke liye /play karo.")
        return

    track = q.popleft()
    now_playing[chat_id] = track
    paused[chat_id]      = False
    seek_pos[chat_id]    = 0
    try:
        await calls.play(chat_id, make_stream(track["filepath"]))
        await send_now_playing(chat_id, track)
    except Exception as e:
        logger.exception("play_next failed")
        await tg_app.bot.send_message(chat_id, f"❌ Error: {e}")

# ─────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 *ShivviXMusician — Voice Chat Music Bot*\n\n"
        "📌 *Commands:*\n"
        "  `/play <song>` – Song bajao\n"
        "  `/pause` – Pause karo\n"
        "  `/resume` – Wapas chalao\n"
        "  `/replay` – Current song firse\n"
        "  `/skip` – Agla song\n"
        "  `/stop` – Band karo\n"
        "  `/queue` – Queue dekho\n"
        "  `/np` – Ab kya chal raha hai\n"
        "  `/loop <0/3/inf>` – Loop set karo\n"
        "  `/shuffle` – Queue shuffle karo\n\n"
        "⚡ Pehle group mein *Voice Chat start karo*, phir /play karo!\n"
        "🎛️ Har song ke saath full control buttons milenge:\n"
        "⏪ Seek · ⏸️ Pause · 🔁 Replay · ⏭️ Skip · 🔀 Shuffle · 🔁 Loop · ⏹️ Stop",
        parse_mode="Markdown"
    )

async def play_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Ye sirf groups mein kaam karta hai!")
        return
    if not ctx.args:
        await update.message.reply_text("❌ Song naam likho!\nExample: `/play Kesariya`", parse_mode="Markdown")
        return

    query     = " ".join(ctx.args)
    requester = update.effective_user.first_name if update.effective_user else "Unknown"
    msg       = await update.message.reply_text(f"🔍 *{query}* dhoondh raha hoon...", parse_mode="Markdown")

    result = await asyncio.get_running_loop().run_in_executor(None, yt_download, query)

    if not result["success"]:
        await msg.edit_text(f"❌ Error: {result['error']}")
        return

    track = {
        "title":        result["title"],
        "filepath":     result["filepath"],
        "duration":     result.get("duration", 0),
        "requested_by": requester,
    }

    if chat_id in now_playing:
        queues.setdefault(chat_id, deque()).append(track)
        pos = len(queues[chat_id])
        await msg.edit_text(
            f"✅ Queue mein add: *{track['title']}*\n📋 Position: #{pos}",
            parse_mode="Markdown"
        )
    else:
        now_playing[chat_id] = track
        paused[chat_id]      = False
        seek_pos[chat_id]    = 0
        try:
            await calls.play(chat_id, make_stream(track["filepath"]))
            await msg.delete()
            await send_now_playing(chat_id, track)
        except Exception as e:
            now_playing.pop(chat_id, None)
            logger.exception("join_group_call failed")
            await msg.edit_text(f"❌ Error: {e}")

async def pause_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Kuch nahi chal raha!")
        return
    await calls.pause(chat_id)
    paused[chat_id] = True
    await update.message.reply_text("⏸️ Pause kar diya!", reply_markup=player_keyboard(chat_id))

async def resume_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Kuch nahi chal raha!")
        return
    await calls.resume(chat_id)
    paused[chat_id] = False
    await update.message.reply_text("▶️ Resume kar diya!", reply_markup=player_keyboard(chat_id))

async def replay_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    track   = now_playing.get(chat_id)
    if not track:
        await update.message.reply_text("❌ Kuch nahi chal raha!")
        return
    seek_pos[chat_id]  = 0
    paused[chat_id]    = False
    await calls.play(chat_id, make_stream(track["filepath"]))
    await update.message.reply_text(
        f"🔁 Firse chalu: *{track['title']}*", parse_mode="Markdown",
        reply_markup=player_keyboard(chat_id)
    )

async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Kuch nahi chal raha!")
        return
    loop_mode[chat_id] = 0          # loop band karo skip pe
    cleanup(now_playing.pop(chat_id, {}).get("filepath"))
    await update.message.reply_text("⏭️ Skip!")
    await play_next(chat_id)

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop_mode.pop(chat_id, None)
    for t in queues.pop(chat_id, []):
        cleanup(t.get("filepath"))
    cleanup(now_playing.pop(chat_id, {}).get("filepath"))
    paused.pop(chat_id, None)
    seek_pos.pop(chat_id, None)
    try:
        await calls.leave(chat_id)
    except Exception:
        pass
    await update.message.reply_text("⏹️ Band kar diya!")

async def queue_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    np      = now_playing.get(chat_id)
    q       = queues.get(chat_id)
    if not np and not q:
        await update.message.reply_text("📋 Queue khaali hai!")
        return
    lines = []
    if np:
        played = seek_pos.get(chat_id, 0)
        total  = np.get("duration", 0)
        bar    = progress_bar(played, total)
        lines.append(
            f"▶️ *Ab:* {np['title']}\n"
            f"   👤 {np.get('requested_by','Unknown')} · "
            f"`{fmt_time(played)}` {bar} `{fmt_time(total)}`"
        )
    if q:
        lines.append("\n📋 *Queue:*")
        for i, t in enumerate(q, 1):
            lines.append(f"  {i}. {t['title']} (by {t.get('requested_by','Unknown')}) — {fmt_time(t.get('duration',0))}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def np_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    np      = now_playing.get(chat_id)
    if not np:
        await update.message.reply_text("❌ Kuch nahi chal raha!")
        return
    played = seek_pos.get(chat_id, 0)
    total  = np.get("duration", 0)
    bar    = progress_bar(played, total)
    lv     = loop_mode.get(chat_id, 0)
    q_cnt  = len(queues.get(chat_id, []))
    text = (
        f"🎵 *Ab Chal Raha Hai*\n\n"
        f"🎶 *{np['title']}*\n"
        f"👤 *Requested by:* {np.get('requested_by','Unknown')}\n"
        f"⏱️ `{fmt_time(played)}` {bar} `{fmt_time(total)}`\n"
        f"🔁 Loop: *{'∞' if lv == -1 else lv if lv > 0 else 'Off'}*"
    )
    if q_cnt:
        text += f"\n📋 Queue mein aur *{q_cnt}* track(s)"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=player_keyboard(chat_id))

async def loop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text(
            "❌ Usage:\n`/loop 0` – Off\n`/loop 3` – 3 baar\n`/loop inf` – infinite",
            parse_mode="Markdown"
        )
        return
    val = ctx.args[0].lower()
    if val in ("inf", "infinite", "-1"):
        loop_mode[chat_id] = -1
        await update.message.reply_text("🔁 Loop: *Infinite* set kar diya!", parse_mode="Markdown")
    elif val.isdigit():
        loop_mode[chat_id] = int(val)
        await update.message.reply_text(f"🔁 Loop: *{val}* baar set kar diya!", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Galat value! 0, 3, inf — kuch bhi likho.")

async def shuffle_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q       = queues.get(chat_id)
    if not q or len(q) < 2:
        await update.message.reply_text("❌ Shuffle ke liye queue mein kam se kam 2 songs chahiye!")
        return
    lst = list(q)
    random.shuffle(lst)
    queues[chat_id] = deque(lst)
    await update.message.reply_text(f"🔀 Queue shuffle kar diya! ({len(lst)} songs)")

# ─────────────────────────────────────────────
#  INLINE BUTTON HANDLER
# ─────────────────────────────────────────────
async def button_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()

    parts      = query.data.split(":", 1)
    action     = parts[0]
    chat_id    = int(parts[1]) if len(parts) > 1 else update.effective_chat.id

    # ── Pause / Resume toggle ──
    if action == "pause":
        if chat_id not in now_playing:
            return
        if paused.get(chat_id):
            await calls.resume(chat_id)
            paused[chat_id] = False
        else:
            await calls.pause(chat_id)
            paused[chat_id] = True
        try:
            await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
        except Exception:
            pass

    # ── Replay ──
    elif action == "replay":
        track = now_playing.get(chat_id)
        if track:
            seek_pos[chat_id] = 0
            paused[chat_id]   = False
            await calls.play(chat_id, make_stream(track["filepath"]))
            try:
                await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
            except Exception:
                pass

    # ── Skip ──
    elif action == "skip":
        if chat_id in now_playing:
            loop_mode[chat_id] = 0
            cleanup(now_playing.pop(chat_id, {}).get("filepath"))
            try:
                await query.edit_message_text("⏭️ Skipped!")
            except Exception:
                pass
            await play_next(chat_id)

    # ── Stop ──
    elif action == "stop":
        loop_mode.pop(chat_id, None)
        for t in queues.pop(chat_id, []):
            cleanup(t.get("filepath"))
        cleanup(now_playing.pop(chat_id, {}).get("filepath"))
        paused.pop(chat_id, None)
        seek_pos.pop(chat_id, None)
        try:
            await calls.leave(chat_id)
        except Exception:
            pass
        try:
            await query.edit_message_text("⏹️ Band kar diya!")
        except Exception:
            pass

    # ── Seek back 10s ──
    elif action == "seek_back":
        seek_pos[chat_id] = max(0, seek_pos.get(chat_id, 0) - 10)
        try:
            await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
        except Exception:
            pass

    # ── Seek forward 10s ──
    elif action == "seek_fwd":
        track = now_playing.get(chat_id)
        if track:
            seek_pos[chat_id] = min(
                track.get("duration", 0),
                seek_pos.get(chat_id, 0) + 10
            )
        try:
            await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
        except Exception:
            pass

    # ── Loop toggle ──
    elif action == "loop":
        lv = loop_mode.get(chat_id, 0)
        if lv == 0:
            loop_mode[chat_id] = 3       # 0 → 3 times
        elif lv == 3:
            loop_mode[chat_id] = -1      # 3 → infinite
        else:
            loop_mode[chat_id] = 0       # infinite → off
        try:
            await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
        except Exception:
            pass

    # ── Shuffle ──
    elif action == "shuffle":
        q = queues.get(chat_id)
        if q and len(q) >= 2:
            lst = list(q)
            random.shuffle(lst)
            queues[chat_id] = deque(lst)
            await query.answer("🔀 Queue shuffle ho gayi!", show_alert=True)
        else:
            await query.answer("❌ Shuffle ke liye 2+ songs chahiye!", show_alert=True)

    # ── Show Queue inline ──
    elif action == "show_queue":
        np = now_playing.get(chat_id)
        q  = queues.get(chat_id)
        if not np and not q:
            await query.answer("📋 Queue khaali hai!", show_alert=True)
            return
        lines = []
        if np:
            lines.append(f"▶️ *Ab:* {np['title']} — {np.get('requested_by','?')}")
        if q:
            lines.append("\n📋 *Queue:*")
            for i, t in enumerate(q, 1):
                lines.append(f"  {i}. {t['title']} ({t.get('requested_by','?')})")
        try:
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            pass

# ─────────────────────────────────────────────
#  Stream end → auto next
# ─────────────────────────────────────────────
@calls.on_update(filters.stream_end)
async def on_end(_, update):
    chat_id = update.chat_id
    track   = now_playing.get(chat_id)
    if track:
        cleanup(track.get("filepath"))
    now_playing.pop(chat_id, None)
    seek_pos.pop(chat_id, None)
    await play_next(chat_id)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def post_init(application):
    """Start pyrogram + pytgcalls inside PTB event loop."""
    await pyro_app.start()
    await calls.start()
    logger.info("✅ Pyrogram + PyTgCalls started.")

async def post_shutdown(application):
    """Stop pyrogram + pytgcalls cleanly."""
    try:
        await calls.stop()
    except Exception:
        pass
    try:
        await pyro_app.stop()
    except Exception:
        pass

def main():
    global tg_app

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    tg_app = application

    application.add_handler(CommandHandler("start",   start))
    application.add_handler(CommandHandler("play",    play_cmd))
    application.add_handler(CommandHandler("pause",   pause_cmd))
    application.add_handler(CommandHandler("resume",  resume_cmd))
    application.add_handler(CommandHandler("replay",  replay_cmd))
    application.add_handler(CommandHandler("skip",    skip_cmd))
    application.add_handler(CommandHandler("stop",    stop_cmd))
    application.add_handler(CommandHandler("queue",   queue_cmd))
    application.add_handler(CommandHandler("np",      np_cmd))
    application.add_handler(CommandHandler("loop",    loop_cmd))
    application.add_handler(CommandHandler("shuffle", shuffle_cmd))
    application.add_handler(CallbackQueryHandler(button_cb))

    logger.info("🎵 ShivviXMusician Bot chal raha hai! Ctrl+C se band karo.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
