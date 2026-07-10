"""
🎵 Telegram Voice Chat Music Bot - ENHANCED WITH VIDEO + AUDIO
ShivviXMusician — Sabse Mast Music Streamer
"""

import os
import sys
import random
import asyncio
import logging
import nest_asyncio
import requests
from io import BytesIO
nest_asyncio.apply()

from pytgcalls import filters
from collections import deque
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality
from pyrogram import Client
import yt_dlp

# ═══════════════════════════════════════════════
#  CONFIG (Use environment variables)
# ═══════════════════════════════════════════════
API_ID         = int(os.environ.get("API_ID", "20764054"))
API_HASH       = os.environ.get("API_HASH", "e4471e35e9ca0f781d70d4d0920f75c1")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8800384691:AAHO9cRVADcPv73PfgpnOJrXyEXQt2Psxpg")
SESSION_STRING = os.environ.get("SESSION", "BQE81ZYAGqjo14uOxdeHiBPQRiCc4yNy6qHE9ml1wYrgVJnJTkjYmKyYLkLGD5KxnhMAgwsfTAUsfADZ6xvMiKa6MlzCmaxQMsyk0SYCiHf4IDjecle7yW-fGWrG54D4wZ8Yb_p3pOyQCuvVj13KFLkck-5kB4Pr4vlH9nCpxw6X8LHXT-blY--xORgbDrXTn-sXVLfrgGjbR1mbvpd4o61tfSLJPpYFXOIm1hF3eyIvs21xFhlWajLQI8iB5GnQPeupp4OjGkolZsZiod789De9NuYs2QVDMGlNM71Zlz_WfBbgGUt-4Ig1qtmLsy3ui4Plwo6nfT3s7iLixXUFqlPU_Zt7gwAAAAINlPQsAA")
DOWNLOAD_DIR   = "downloads"

AUDIO_QUALITY = getattr(AudioQuality, os.environ.get("AUDIO_QUALITY", "MEDIUM"))

# ═══════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Global state
queues: dict      = {}
now_playing: dict = {}
paused: dict      = {}
loop_mode: dict   = {}
seek_pos: dict    = {}
is_video: dict    = {}  # Track if current stream is video
tg_app = None

# Pyrogram + PyTgCalls setup
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
    logger.error(f"❌ Failed to init Pyrogram: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────
#  YOUTUBE DOWNLOAD (Audio + Video)
# ─────────────────────────────────────────────
def yt_download(query: str, prefer_video: bool = True) -> dict:
    """Download from YouTube - video if available, fallback to audio"""
    
    # Video-first format selection
    if prefer_video:
        format_str = (
            "bestvideo[ext=mp4][height<=720]+"
            "bestaudio[ext=m4a]/bestvideo[ext=mp4]+"
            "bestaudio[ext=m4a]/best[ext=mp4]/best"
        )
    else:
        format_str = "bestaudio/best"
    
    ydl_opts = {
        "format": format_str,
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "noplaylist": True,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
    }
    
    # Add audio postprocessor only for non-video
    if not prefer_video:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"🔍 Searching: {query}")
            info = ydl.extract_info(query, download=True)
            
            if "entries" in info:
                info = info["entries"][0]
            
            video_id = info.get("id", "unknown")
            title = info.get("title", "Unknown")
            duration = info.get("duration", 0)
            
            # Find the downloaded file
            possible_exts = ["mp4", "mkv", "webm", "mp3", "m4a", "opus", "vorbis"]
            filepath = None
            for ext in possible_exts:
                candidate = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
                if os.path.exists(candidate):
                    filepath = candidate
                    break
            
            if not filepath:
                logger.warning(f"⚠️ File not found for {video_id}, trying directory scan")
                for f in os.listdir(DOWNLOAD_DIR):
                    if video_id in f:
                        filepath = os.path.join(DOWNLOAD_DIR, f)
                        break
            
            if not filepath:
                raise Exception(f"Downloaded file not found for {video_id}")
            
            # Thumbnail
            thumbnail = info.get("thumbnail", None)
            
            return {
                "success": True,
                "filepath": filepath,
                "title": title,
                "duration": duration,
                "video_id": video_id,
                "thumbnail": thumbnail,
                "is_video": prefer_video and filepath.endswith((".mp4", ".mkv", ".webm")),
            }
    
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        return {"success": False, "error": str(e), "is_video": False}

def cleanup(filepath: str):
    """Safe cleanup of downloaded files"""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"🗑️ Cleaned: {filepath}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")

def make_stream(filepath: str, is_video: bool = False):
    """Create media stream for playback"""
    try:
        # pytgcalls 2.1.0 auto-detects video from file type
        # If file is MP4/MKV, it will stream video; otherwise audio only
        return MediaStream(
            filepath,
            audio_parameters=AUDIO_QUALITY
        )
    except Exception as e:
        logger.warning(f"Stream creation error: {e}")
        return MediaStream(filepath, audio_parameters=AUDIO_QUALITY)

def fmt_time(seconds: int) -> str:
    """Format seconds to MM:SS"""
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"

# ─────────────────────────────────────────────
#  PROGRESS BAR
# ─────────────────────────────────────────────
def progress_bar(played: int, total: int, length: int = 10) -> str:
    """Generate progress bar"""
    if total <= 0:
        return "▱" * length
    filled = int(length * min(played, total) / total)
    return "▰" * filled + "▱" * (length - filled)

# ─────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ─────────────────────────────────────────────
def player_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Build player control keyboard"""
    is_paused = paused.get(chat_id, False)
    loop_val = loop_mode.get(chat_id, 0)
    loop_label = f"🔁 Loop: {'∞' if loop_val == -1 else loop_val if loop_val > 0 else 'Off'}"
    pause_btn = InlineKeyboardButton(
        "▶️ Resume" if is_paused else "⏸️ Pause",
        callback_data=f"pause:{chat_id}"
    )
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏪ 10s", callback_data=f"seek_back:{chat_id}"),
            pause_btn,
            InlineKeyboardButton("⏩ 10s", callback_data=f"seek_fwd:{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔁 Replay", callback_data=f"replay:{chat_id}"),
            InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{chat_id}"),
            InlineKeyboardButton("📋 Queue", callback_data=f"show_queue:{chat_id}"),
        ],
        [
            InlineKeyboardButton(loop_label, callback_data=f"loop:{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle:{chat_id}"),
        ],
        [InlineKeyboardButton("⏹️ Stop", callback_data=f"stop:{chat_id}")],
    ])

# ─────────────────────────────────────────────
#  NOW PLAYING WITH PHOTO/THUMBNAIL
# ─────────────────────────────────────────────
async def send_now_playing(chat_id: int, track: dict):
    """Send now playing message with thumbnail"""
    played = seek_pos.get(chat_id, 0)
    total = track.get("duration", 0)
    bar = progress_bar(played, total, length=15)
    q_cnt = len(queues.get(chat_id, []))
    
    # Check if video is streaming
    is_vid = is_video.get(chat_id, False)
    stream_type = "🎬 Video + Audio" if is_vid else "🎵 Audio"
    
    caption = (
        f"🎵 <b>Ab Chal Raha Hai</b>\n\n"
        f"<b>🎶 {track['title']}</b>\n"
        f"👤 <b>Requested by:</b> {track.get('requested_by', 'Unknown')}\n"
        f"📡 <b>Type:</b> {stream_type}\n"
        f"⏱️ <code>{fmt_time(played)} {bar} {fmt_time(total)}</code>"
    )
    
    if q_cnt:
        caption += f"\n📋 Queue mein aur <b>{q_cnt}</b> track(s)"
    
    # Try to send with thumbnail
    thumbnail = track.get("thumbnail")
    try:
        if thumbnail:
            await tg_app.bot.send_photo(
                chat_id,
                photo=thumbnail,
                caption=caption,
                parse_mode="HTML",
                reply_markup=player_keyboard(chat_id)
            )
        else:
            # Fallback to text message
            await tg_app.bot.send_message(
                chat_id,
                caption,
                parse_mode="HTML",
                reply_markup=player_keyboard(chat_id)
            )
    except Exception as e:
        logger.error(f"Error sending now_playing: {e}")
        try:
            await tg_app.bot.send_message(
                chat_id,
                caption,
                parse_mode="HTML",
                reply_markup=player_keyboard(chat_id)
            )
        except Exception as e2:
            logger.error(f"Fallback also failed: {e2}")

# ─────────────────────────────────────────────
#  PLAYBACK ENGINE
# ─────────────────────────────────────────────
async def play_next(chat_id: int):
    """Play next track in queue"""
    # Check loop mode
    lv = loop_mode.get(chat_id, 0)
    if lv != 0 and chat_id in now_playing:
        track = now_playing[chat_id]
        if lv > 0:
            loop_mode[chat_id] = lv - 1
        seek_pos[chat_id] = 0
        paused[chat_id] = False
        try:
            is_vid = is_video.get(chat_id, False)
            await calls.play(chat_id, make_stream(track["filepath"], is_vid))
            await send_now_playing(chat_id, track)
        except Exception as e:
            logger.error(f"❌ Loop replay error: {e}")
            await tg_app.bot.send_message(chat_id, f"❌ Error: {e}")
        return
    
    # Get next from queue
    q = queues.get(chat_id)
    if not q:
        now_playing.pop(chat_id, None)
        paused.pop(chat_id, None)
        seek_pos.pop(chat_id, None)
        loop_mode.pop(chat_id, None)
        is_video.pop(chat_id, None)
        await tg_app.bot.send_message(
            chat_id,
            "✅ Queue khatam! 🎵\nAur songs ke liye `/play song_name` karo.",
            parse_mode="Markdown"
        )
        return
    
    track = q.popleft()
    now_playing[chat_id] = track
    paused[chat_id] = False
    seek_pos[chat_id] = 0
    is_video[chat_id] = track.get("is_video", False)
    
    try:
        await calls.play(chat_id, make_stream(track["filepath"], track.get("is_video", False)))
        await send_now_playing(chat_id, track)
    except Exception as e:
        logger.error(f"❌ play_next error: {e}")
        await tg_app.bot.send_message(chat_id, f"❌ Playback error: {e}")
        await play_next(chat_id)

# ─────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Welcome message with full features"""
    
    # Custom formatted welcome message in small caps style
    welcome_text = (
        "ʜᴇʏ, {} !\n\n"
        "ɪ'ᴍ <b>ShivviXMusician</b> 🎵\n\n"
        "┏━━━━━━━━━━━━━━━━━⧫\n"
        "┠ ◆ ɪ ʜᴀᴠᴇ sᴘᴇᴄɪᴀʟ ғᴇᴀᴛᴜʀᴇs.\n"
        "┠ ◆ ᴀʟʟ-ɪɴ-ᴏɴᴇ ʙᴏᴛ.\n"
        "┗━━━━━━━━━━━━━━━━━⧫\n\n"
        "┏━━━━━━━━━━━━━━━━━⧫\n"
        "┠ ◆ ʏᴏᴜ ᴄᴀɴ ᴘʟᴀʏ ꜱᴏɴɢꜱ ɪɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ.\n"
        "┠ ◆ ᴀᴜᴅɪᴏ + ᴠɪᴅᴇᴏ sᴜᴘᴘᴏʀᴛ.\n"
        "┠ ◆ ᴀᴅᴠᴀɴᴄᴇᴅ qᴜᴇᴜᴇ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ.\n"
        "┠ ◆ ʟᴏᴏᴘ, sʜᴜғғʟᴇ, ᴘʀᴏɢʀᴇss ᴛʀᴀᴄᴋɪɴɢ.\n"
        "┠ ◆ ᴡᴏʀᴋs ɪɴ ᴀʟʟ ɢʀᴏᴜᴘs.\n"
        "┗━━━━━━━━━━━━━━━━━⧫\n\n"
        "๏ ᴄʟɪᴄᴋ ᴏɴ ᴛʜᴇ <b>ʜᴇʟᴘ</b> ʙᴜᴛᴛᴏɴ ᴛᴏ ɢᴇᴛ ɪɴғᴏʀᴍᴀᴛɪᴏɴ ᴀʙᴏᴜᴛ ᴍʏ ᴄᴏᴍᴍᴀɴᴅs.\n"
    ).format(update.effective_user.first_name or "Friend")
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 Play", callback_data="play_help"),
            InlineKeyboardButton("📚 Help", callback_data="help"),
        ],
        [InlineKeyboardButton("➕ Add to Group", url="https://t.me/ShivviXMusician_bot?startgroup=true")],
    ])
    
    await update.message.reply_text(welcome_text, parse_mode="HTML", reply_markup=keyboard)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = (
        "<b>📚 COMMANDS & FEATURES</b>\n\n"
        "<b>🎵 Playback:</b>\n"
        "/play <song> — Play any song\n"
        "/pause — Pause playback\n"
        "/resume — Resume playback\n"
        "/skip — Next song\n"
        "/stop — Stop & leave\n\n"
        
        "<b>📋 Queue:</b>\n"
        "/queue — Show queue\n"
        "/shuffle — Shuffle queue\n"
        "/loop [0/3/inf] — Loop mode\n\n"
        
        "<b>ℹ️ Info:</b>\n"
        "/np — Now playing\n"
        "/video — Video streaming info\n\n"
        
        "<b>💡 TIP:</b> Use inline buttons for quick control!"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

async def video_info_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Video streaming info"""
    video_text = (
        "<b>🎬 VIDEO STREAMING INFO</b>\n\n"
        
        "<b>✅ Video Chalega Jab:</b>\n"
        "• Group/Channel mein <b>Video Chat</b> enabled ho\n"
        "• YouTube video available ho (MP4 format)\n"
        "• FFmpeg properly installed ho\n\n"
        
        "<b>📡 Stream Types:</b>\n"
        "🎬 <b>Video Chat:</b> MP4 Video + Audio streaming\n"
        "🎵 <b>Voice Chat:</b> Audio only streaming\n\n"
        
        "<b>⚙️ Audio Quality:</b>\n"
        f"• Current: {os.environ.get('AUDIO_QUALITY', 'MEDIUM')}\n"
        "• Options: NORMAL, MEDIUM, HIGH, VERY_HIGH\n"
        "• Bitrate: 128-320 kbps\n\n"
        
        "<b>🚀 Railway Features:</b>\n"
        "✓ FFmpeg pre-installed\n"
        "✓ MP4 video download\n"
        "✓ Auto quality adaptation\n"
        "✓ Thumbnail support\n\n"
        
        "<b>💡 Pro Tips:</b>\n"
        "1. Video Chat mein /play karo\n"
        "2. Faster internet = smoother playback\n"
        "3. Download time ~30-60 seconds\n"
        "4. Processing time ~10-30 seconds"
    )
    await update.message.reply_text(video_text, parse_mode="HTML")

async def play_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Play command"""
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "Unknown"
    
    if not ctx.args:
        await update.message.reply_text("🎵 Usage: /play <song name>\nExample: /play Tum Hi Ho")
        return
    
    query = " ".join(ctx.args)
    await update.message.reply_text(f"🔍 Searching: <b>{query}</b>...", parse_mode="HTML")
    
    # Download (try video first, fallback to audio)
    result = yt_download(query, prefer_video=True)
    if not result.get("success"):
        fallback = yt_download(query, prefer_video=False)
        result = fallback if fallback.get("success") else result
    
    if not result.get("success"):
        await update.message.reply_text(f"❌ Error: {result.get('error', 'Unknown error')}")
        return
    
    track = {
        **result,
        "requested_by": user_name,
    }
    
    # Initialize queue if needed
    if chat_id not in queues:
        queues[chat_id] = deque()
    
    # If nothing playing, play directly
    if chat_id not in now_playing:
        now_playing[chat_id] = track
        paused[chat_id] = False
        seek_pos[chat_id] = 0
        is_video[chat_id] = track.get("is_video", False)
        
        try:
            await calls.play(chat_id, make_stream(track["filepath"], track.get("is_video", False)))
            await send_now_playing(chat_id, track)
        except Exception as e:
            logger.error(f"❌ Play error: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    else:
        # Add to queue
        queues[chat_id].append(track)
        q_len = len(queues[chat_id])
        await update.message.reply_text(
            f"📋 <b>{track['title']}</b> added to queue at position <b>#{q_len}</b>",
            parse_mode="HTML"
        )

async def pause_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Nothing playing!")
        return
    try:
        await calls.pause(chat_id)
        paused[chat_id] = True
        await update.message.reply_text("⏸️ Paused")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def resume_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Nothing playing!")
        return
    try:
        await calls.resume(chat_id)
        paused[chat_id] = False
        await update.message.reply_text("▶️ Resumed")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def replay_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    track = now_playing.get(chat_id)
    if not track:
        await update.message.reply_text("❌ Nothing playing!")
        return
    try:
        seek_pos[chat_id] = 0
        paused[chat_id] = False
        await calls.play(chat_id, make_stream(track["filepath"], track.get("is_video", False)))
        await update.message.reply_text("🔁 Replaying...")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in now_playing:
        await update.message.reply_text("❌ Nothing playing!")
        return
    try:
        loop_mode[chat_id] = 0
        cleanup(now_playing.pop(chat_id, {}).get("filepath"))
        await update.message.reply_text("⏭️ Skipped!")
        await play_next(chat_id)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop_mode.pop(chat_id, None)
    for t in queues.pop(chat_id, []):
        cleanup(t.get("filepath"))
    cleanup(now_playing.pop(chat_id, {}).get("filepath"))
    paused.pop(chat_id, None)
    seek_pos.pop(chat_id, None)
    is_video.pop(chat_id, None)
    try:
        await calls.leave(chat_id)
        await update.message.reply_text("⏹️ Stopped!")
    except Exception:
        await update.message.reply_text("⏹️ Stopped!")

async def queue_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    np = now_playing.get(chat_id)
    q = queues.get(chat_id, [])
    
    if not np and not q:
        await update.message.reply_text("📋 Queue khaali hai!")
        return
    
    lines = []
    if np:
        lines.append(f"▶️ <b>Ab Chal Raha Hai:</b> {np['title']}")
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
    await send_now_playing(chat_id, track)

async def loop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("Usage: /loop 0|3|inf")
        return
    
    arg = ctx.args[0].lower()
    if arg == "0":
        loop_mode[chat_id] = 0
        await update.message.reply_text("🔁 Loop off")
    elif arg == "3":
        loop_mode[chat_id] = 3
        await update.message.reply_text("🔁 Loop 3 times")
    elif arg in ("inf", "infinity"):
        loop_mode[chat_id] = -1
        await update.message.reply_text("🔁 Loop infinite")
    else:
        await update.message.reply_text("❌ Use: 0, 3, or inf")

async def shuffle_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q = queues.get(chat_id, [])
    if len(q) < 2:
        await update.message.reply_text("❌ Need 2+ songs to shuffle!")
        return
    lst = list(q)
    random.shuffle(lst)
    queues[chat_id] = deque(lst)
    await update.message.reply_text(f"🔀 Shuffled {len(lst)} songs!")

# ─────────────────────────────────────────────
#  CALLBACK HANDLERS
# ─────────────────────────────────────────────
async def button_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    chat_id = update.effective_chat.id
    
    if data == "play_help":
        await query.message.reply_text("🎵 Use: /play <song name>\nExample: /play Tum Hi Ho")
        return
    elif data == "help":
        help_text = (
            "<b>📚 COMMANDS</b>\n"
            "/play - Play song\n"
            "/pause - Pause\n"
            "/resume - Resume\n"
            "/skip - Next\n"
            "/stop - Stop\n"
            "/queue - Queue\n"
            "/loop [0/3/inf] - Loop"
        )
        await query.message.reply_text(help_text, parse_mode="HTML")
        return
    
    # Playback controls
    if ":" not in data:
        return
    
    action, target_chat = data.split(":", 1)
    target_chat = int(target_chat)
    
    try:
        if action == "pause":
            if target_chat not in now_playing:
                return
            if paused.get(target_chat):
                await calls.resume(target_chat)
                paused[target_chat] = False
            else:
                await calls.pause(target_chat)
                paused[target_chat] = True
            try:
                await query.edit_message_reply_markup(reply_markup=player_keyboard(target_chat))
            except:
                pass
        
        elif action == "replay":
            track = now_playing.get(target_chat)
            if track:
                seek_pos[target_chat] = 0
                paused[target_chat] = False
                await calls.play(target_chat, make_stream(track["filepath"], track.get("is_video", False)))
        
        elif action == "skip":
            if target_chat in now_playing:
                loop_mode[target_chat] = 0
                cleanup(now_playing.pop(target_chat, {}).get("filepath"))
                try:
                    await query.edit_message_text("⏭️ Skipped!")
                except:
                    pass
                await play_next(target_chat)
        
        elif action == "stop":
            loop_mode.pop(target_chat, None)
            for t in queues.pop(target_chat, []):
                cleanup(t.get("filepath"))
            cleanup(now_playing.pop(target_chat, {}).get("filepath"))
            paused.pop(target_chat, None)
            seek_pos.pop(target_chat, None)
            is_video.pop(target_chat, None)
            try:
                await calls.leave(target_chat)
            except:
                pass
            try:
                await query.edit_message_text("⏹️ Stopped!")
            except:
                pass
        
        elif action == "seek_back":
            seek_pos[target_chat] = max(0, seek_pos.get(target_chat, 0) - 10)
            try:
                await query.edit_message_reply_markup(reply_markup=player_keyboard(target_chat))
            except:
                pass
        
        elif action == "seek_fwd":
            track = now_playing.get(target_chat)
            if track:
                seek_pos[target_chat] = min(track.get("duration", 0), seek_pos.get(target_chat, 0) + 10)
            try:
                await query.edit_message_reply_markup(reply_markup=player_keyboard(target_chat))
            except:
                pass
        
        elif action == "loop":
            lv = loop_mode.get(target_chat, 0)
            if lv == 0:
                loop_mode[target_chat] = 3
            elif lv == 3:
                loop_mode[target_chat] = -1
            else:
                loop_mode[target_chat] = 0
            try:
                await query.edit_message_reply_markup(reply_markup=player_keyboard(target_chat))
            except:
                pass
        
        elif action == "shuffle":
            q = queues.get(target_chat, [])
            if q and len(q) >= 2:
                lst = list(q)
                random.shuffle(lst)
                queues[target_chat] = deque(lst)
                await query.answer("🔀 Shuffled!", show_alert=False)
            else:
                await query.answer("❌ Need 2+ songs!", show_alert=True)
        
        elif action == "show_queue":
            np = now_playing.get(target_chat)
            q = queues.get(target_chat, [])
            if not np and not q:
                await query.answer("📋 Queue khaali!", show_alert=True)
                return
            lines = []
            if np:
                lines.append(f"▶️ <b>Ab:</b> {np['title']}")
            if q:
                lines.append("\n📋 <b>Queue:</b>")
                for i, t in enumerate(q, 1):
                    lines.append(f"  {i}. {t['title']}")
            try:
                await query.message.reply_text("\n".join(lines), parse_mode="HTML")
            except:
                pass
    
    except Exception as e:
        logger.error(f"Button callback error: {e}")

# ─────────────────────────────────────────────
#  STREAM END HANDLER
# ─────────────────────────────────────────────
@calls.on_update(filters.stream_end)
async def on_stream_end(_, update):
    chat_id = update.chat_id
    track = now_playing.get(chat_id)
    if track:
        cleanup(track.get("filepath"))
    now_playing.pop(chat_id, None)
    seek_pos.pop(chat_id, None)
    await play_next(chat_id)

# ─────────────────────────────────────────────
#  BOT STARTUP / SHUTDOWN
# ─────────────────────────────────────────────
async def post_init(application):
    """Initialize pyrogram + pytgcalls"""
    try:
        await pyro_app.start()
        await calls.start()
        logger.info("✅ Bot started successfully!")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")
        raise

async def post_shutdown(application):
    """Cleanup on shutdown"""
    try:
        await calls.stop()
    except Exception:
        pass
    try:
        await pyro_app.stop()
    except Exception:
        pass
    logger.info("🛑 Bot shutdown complete")

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    global tg_app
    
    logger.info("🎵 Starting ShivviXMusician Bot...")
    
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    tg_app = application
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("video", video_info_cmd))
    application.add_handler(CommandHandler("play", play_cmd))
    application.add_handler(CommandHandler("pause", pause_cmd))
    application.add_handler(CommandHandler("resume", resume_cmd))
    application.add_handler(CommandHandler("replay", replay_cmd))
    application.add_handler(CommandHandler("skip", skip_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("queue", queue_cmd))
    application.add_handler(CommandHandler("np", np_cmd))
    application.add_handler(CommandHandler("loop", loop_cmd))
    application.add_handler(CommandHandler("shuffle", shuffle_cmd))
    application.add_handler(CallbackQueryHandler(button_cb))
    
    logger.info("🎵 ShivviXMusician ready! Running on Railway...")
    application.run_polling(drop_pending_updates=True, allowed_updates=None)

if __name__ == "__main__":
    main()
