"""
🎵 Telegram Voice Chat Music Bot - STABLE VERSION
ShivviXMusician — Sabse Mast Music Streamer
Simplified for pytgcalls 1.0.0 stability
"""

import os
import sys
import random
import asyncio
import logging
import nest_asyncio
from collections import deque

nest_asyncio.apply()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
    from pytgcalls import filters
except ImportError as e:
    print(f"❌ pytgcalls import error: {e}")
    sys.exit(1)

from pyrogram import Client
import yt_dlp

# ═══════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════
API_ID         = int(os.environ.get("API_ID", "20764054"))
API_HASH       = os.environ.get("API_HASH", "e4471e35e9ca0f781d70d4d0920f75c1")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8800384691:AAHO9cRVADcPv73PfgpnOJrXyEXQt2Psxpg")
SESSION_STRING = os.environ.get("SESSION", "BQE81ZYAGqjo14uOxdeHiBPQRiCc4yNy6qHE9ml1wYrgVJnJTkjYmKyYLkLGD5KxnhMAgwsfTAUsfADZ6xvMiKa6MlzCmaxQMsyk0SYCiHf4IDjecle7yW-fGWrG54D4wZ8Yb_p3pOyQCuvVj13KFLkck-5kB4Pr4vlH9nCpxw6X8LHXT-blY--xORgbDrXTn-sXVLfrgGjbR1mbvpd4o61tfSLJPpYFXOIm1hF3eyIvs21xFhlWajLQI8iB5GnQPeupp4OjGkolZsZiod789De9NuYs2QVDMGlNM71Zlz_WfBbgGUt-4Ig1qtmLsy3ui4Plwo6nfT3s7iLixXUFqlPU_Zt7gwAAAAINlPQsAA")
DOWNLOAD_DIR   = "downloads"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Global state
queues = {}
now_playing = {}
paused = {}
loop_mode = {}
seek_pos = {}
tg_app = None

# Pyrogram + PyTgCalls
try:
    pyro_app = Client(
        name="music_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        no_updates=True,
    )
    calls = PyTgCalls(pyro_app)
    logger.info("✅ Pyrogram + PyTgCalls initialized")
except Exception as e:
    logger.error(f"❌ Init error: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────
#  YOUTUBE DOWNLOAD
# ─────────────────────────────────────────────
def yt_download(query: str) -> dict:
    """Download audio from YouTube"""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"🔍 Searching: {query}")
            info = ydl.extract_info(query, download=True)
            
            if "entries" in info:
                info = info["entries"][0]
            
            video_id = info.get("id", "unknown")
            filepath = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
            
            if not os.path.exists(filepath):
                for f in os.listdir(DOWNLOAD_DIR):
                    if video_id in f:
                        filepath = os.path.join(DOWNLOAD_DIR, f)
                        break
            
            return {
                "success": True,
                "filepath": filepath,
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", None),
            }
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        return {"success": False, "error": str(e)}

def cleanup(filepath: str):
    """Delete downloaded file"""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except:
        pass

def fmt_time(seconds: int) -> str:
    """Format time"""
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"

# ─────────────────────────────────────────────
#  KEYBOARD
# ─────────────────────────────────────────────
def player_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Player controls"""
    is_paused = paused.get(chat_id, False)
    pause_btn = InlineKeyboardButton(
        "▶️ Resume" if is_paused else "⏸️ Pause",
        callback_data=f"pause:{chat_id}"
    )
    
    return InlineKeyboardMarkup([
        [pause_btn, InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{chat_id}")],
        [InlineKeyboardButton("📋 Queue", callback_data=f"queue:{chat_id}")],
        [InlineKeyboardButton("⏹️ Stop", callback_data=f"stop:{chat_id}")],
    ])

# ─────────────────────────────────────────────
#  PLAYBACK
# ─────────────────────────────────────────────
async def play_next(chat_id: int):
    """Play next track"""
    q = queues.get(chat_id)
    if not q:
        now_playing.pop(chat_id, None)
        await tg_app.bot.send_message(chat_id, "✅ Queue khatam!")
        return
    
    track = q.popleft()
    now_playing[chat_id] = track
    paused[chat_id] = False
    
    try:
        stream = MediaStream(track["filepath"])
        await calls.play(chat_id, stream)
        
        text = f"🎵 <b>{track['title']}</b>\n"
        text += f"👤 Requested: {track.get('requested_by', '?')}"
        
        await tg_app.bot.send_photo(
            chat_id,
            photo=track.get("thumbnail", "https://via.placeholder.com/200"),
            caption=text,
            parse_mode="HTML",
            reply_markup=player_keyboard(chat_id)
        )
    except Exception as e:
        logger.error(f"Play error: {e}")
        await play_next(chat_id)

# ─────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    text = (
        "ʜᴇʏ! 🎵\n\n"
        "ɪ'ᴍ <b>ShivviXMusician</b> ✨\n\n"
        "✅ Voice chat streaming\n"
        "✅ YouTube search\n"
        "✅ Queue management\n"
        "✅ Loop & shuffle\n\n"
        "Use /play <song> to start! 🎶"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Play", callback_data="help")],
    ])
    
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Help"""
    text = (
        "<b>📚 COMMANDS</b>\n\n"
        "/play <song> — Play song 🎵\n"
        "/pause — Pause\n"
        "/resume — Resume\n"
        "/skip — Next\n"
        "/stop — Stop\n"
        "/queue — Queue\n"
        "/np — Now playing"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def play_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Play command"""
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "User"
    
    if not ctx.args:
        await update.message.reply_text("🎵 Usage: /play <song name>")
        return
    
    query = " ".join(ctx.args)
    await update.message.reply_text(f"🔍 Searching: <b>{query}</b>...", parse_mode="HTML")
    
    result = yt_download(query)
    if not result.get("success"):
        await update.message.reply_text(f"❌ Error: {result.get('error')}")
        return
    
    track = {**result, "requested_by": user_name}
    
    if chat_id not in queues:
        queues[chat_id] = deque()
    
    if chat_id not in now_playing:
        now_playing[chat_id] = track
        paused[chat_id] = False
        
        try:
            stream = MediaStream(track["filepath"])
            await calls.play(chat_id, stream)
            
            text = f"🎵 <b>{track['title']}</b>\n"
            text += f"👤 {track.get('requested_by', '?')}"
            
            await tg_app.bot.send_photo(
                chat_id,
                photo=track.get("thumbnail", "https://via.placeholder.com/200"),
                caption=text,
                parse_mode="HTML",
                reply_markup=player_keyboard(chat_id)
            )
        except Exception as e:
            logger.error(f"Play error: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    else:
        queues[chat_id].append(track)
        await update.message.reply_text(
            f"📋 <b>{track['title']}</b> added to queue",
            parse_mode="HTML"
        )

async def pause_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        await calls.pause(chat_id)
        paused[chat_id] = True
        await update.message.reply_text("⏸️ Paused")
    except:
        await update.message.reply_text("❌ Error")

async def resume_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        await calls.resume(chat_id)
        paused[chat_id] = False
        await update.message.reply_text("▶️ Resumed")
    except:
        await update.message.reply_text("❌ Error")

async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in now_playing:
        cleanup(now_playing[chat_id].get("filepath"))
        now_playing.pop(chat_id, None)
        await play_next(chat_id)
    else:
        await update.message.reply_text("❌ Nothing playing!")

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for t in queues.pop(chat_id, []):
        cleanup(t.get("filepath"))
    cleanup(now_playing.pop(chat_id, {}).get("filepath"))
    paused.pop(chat_id, None)
    try:
        await calls.leave(chat_id)
    except:
        pass
    await update.message.reply_text("⏹️ Stopped!")

async def queue_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    np = now_playing.get(chat_id)
    q = queues.get(chat_id, [])
    
    if not np and not q:
        await update.message.reply_text("📋 Queue khaali!")
        return
    
    lines = []
    if np:
        lines.append(f"▶️ <b>Now:</b> {np['title']}")
    if q:
        lines.append("\n📋 <b>Queue:</b>")
        for i, t in enumerate(q, 1):
            lines.append(f"  {i}. {t['title']}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def np_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    track = now_playing.get(chat_id)
    if not track:
        await update.message.reply_text("❌ Nothing playing!")
        return
    
    text = f"🎵 <b>{track['title']}</b>"
    await update.message.reply_photo(
        chat_id=chat_id,
        photo=track.get("thumbnail", "https://via.placeholder.com/200"),
        caption=text,
        parse_mode="HTML"
    )

# ─────────────────────────────────────────────
#  CALLBACKS
# ─────────────────────────────────────────────
async def button_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if ":" not in data:
        return
    
    action, chat_id = data.split(":", 1)
    chat_id = int(chat_id)
    
    try:
        if action == "pause":
            if paused.get(chat_id):
                await calls.resume(chat_id)
                paused[chat_id] = False
            else:
                await calls.pause(chat_id)
                paused[chat_id] = True
            await query.edit_message_reply_markup(reply_markup=player_keyboard(chat_id))
        
        elif action == "skip":
            if chat_id in now_playing:
                cleanup(now_playing[chat_id].get("filepath"))
                now_playing.pop(chat_id, None)
                await play_next(chat_id)
        
        elif action == "stop":
            for t in queues.pop(chat_id, []):
                cleanup(t.get("filepath"))
            cleanup(now_playing.pop(chat_id, {}).get("filepath"))
            try:
                await calls.leave(chat_id)
            except:
                pass
            await query.edit_message_text("⏹️ Stopped!")
        
        elif action == "queue":
            np = now_playing.get(chat_id)
            q = queues.get(chat_id, [])
            if not np and not q:
                await query.answer("📋 Queue khaali!", show_alert=True)
            else:
                lines = []
                if np:
                    lines.append(f"▶️ {np['title']}")
                if q:
                    for i, t in enumerate(q, 1):
                        lines.append(f"{i}. {t['title']}")
                await query.message.reply_text("\n".join(lines))
    
    except Exception as e:
        logger.error(f"Callback error: {e}")

# ─────────────────────────────────────────────
#  STREAM END
# ─────────────────────────────────────────────
@calls.on_update(filters.stream_end)
async def on_end(_, update):
    chat_id = update.chat_id
    track = now_playing.get(chat_id)
    if track:
        cleanup(track.get("filepath"))
    now_playing.pop(chat_id, None)
    await play_next(chat_id)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def post_init(application):
    try:
        await pyro_app.start()
        await calls.start()
        logger.info("✅ Bot started!")
    except Exception as e:
        logger.error(f"❌ Start error: {e}")
        raise

async def post_shutdown(application):
    try:
        await calls.stop()
        await pyro_app.stop()
    except:
        pass

def main():
    global tg_app
    
    logger.info("🎵 Starting ShivviXMusician...")
    
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    tg_app = application
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("play", play_cmd))
    application.add_handler(CommandHandler("pause", pause_cmd))
    application.add_handler(CommandHandler("resume", resume_cmd))
    application.add_handler(CommandHandler("skip", skip_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("queue", queue_cmd))
    application.add_handler(CommandHandler("np", np_cmd))
    application.add_handler(CallbackQueryHandler(button_cb))
    
    logger.info("🎵 Bot ready! Running on Railway...")
    application.run_polling(drop_pending_updates=True, allowed_updates=None)

if __name__ == "__main__":
    main()
