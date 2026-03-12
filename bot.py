"""
The Deal Hunter - Telegram Affiliate Bot
Fixed for python-telegram-bot v21 + Render.com deployment
"""

import os
import re
import asyncio
import logging
import feedparser
import httpx
import urllib.parse
from datetime import datetime, timedelta

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError, RetryAfter, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("deal_hunter.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("DealHunterBot")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID  = os.environ.get("CHANNEL_ID", "@The_Deal_Hunter_Official")
EARNKARO_ID = os.environ.get("EARNKARO_ID", "")
ADMIN_IDS   = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
AUTO_POST_INTERVAL = int(os.environ.get("AUTO_POST_INTERVAL", "60"))

RSS_FEEDS = [
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "https://www.dealnews.com/c142/Electronics/?rss=1",
]

posted_urls: set = set()

# ─────────────────────────────────────────────
# EARNKARO LINK
# ─────────────────────────────────────────────
def build_affiliate_link(url: str) -> str:
    encoded = urllib.parse.quote(url, safe="")
    return f"https://earnkaro.com/shareprod?url={encoded}&id={EARNKARO_ID}"

# ─────────────────────────────────────────────
# METADATA SCRAPER
# ─────────────────────────────────────────────
@retry(
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(4),
    reraise=False,
)
async def fetch_meta(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    def og(prop):
        t = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return t["content"].strip() if t and t.get("content") else None

    title    = og("og:title") or (soup.title.string.strip() if soup.title else "🔥 Hot Deal!")
    image    = og("og:image") or og("twitter:image")
    desc     = og("og:description") or ""
    price_m  = re.search(r"[₹\$€£]\s?[\d,]+(?:\.\d{1,2})?", r.text)
    disc_m   = re.search(r"(\d{2,3})\s*%\s*off", r.text, re.IGNORECASE)

    return {
        "title":    title[:180],
        "image":    image,
        "desc":     desc[:250],
        "price":    price_m.group(0) if price_m else None,
        "discount": f"{disc_m.group(1)}% OFF" if disc_m else None,
    }

# ─────────────────────────────────────────────
# FORMAT MESSAGE
# ─────────────────────────────────────────────
def format_message(meta: dict, link: str) -> str:
    parts = [f"🔥 *{meta['title']}*\n"]
    if meta.get("discount"):
        parts.append(f"🎉 *{meta['discount']}*")
    if meta.get("price"):
        parts.append(f"💰 Price: *{meta['price']}*")
    if meta.get("desc"):
        parts.append(f"📝 {meta['desc']}")
    parts += [
        "\n━━━━━━━━━━━━━━━━━━",
        f"🛒 [👉 Grab This Deal Now!]({link})",
        "━━━━━━━━━━━━━━━━━━\n",
        "⚡ *Limited Time — Act Fast!*",
        "📢 Join: @The\\_Deal\\_Hunter\\_Official",
        "🔔 Turn on notifications!",
    ]
    return "\n".join(parts)

# ─────────────────────────────────────────────
# SEND DEAL
# ─────────────────────────────────────────────
async def send_deal(bot: Bot, url: str) -> bool:
    try:
        link = build_affiliate_link(url)
        try:
            meta = await fetch_meta(url)
        except Exception as e:
            logger.warning(f"Meta fetch failed: {e}")
            meta = {"title": "🔥 Hot Deal!", "image": None, "desc": "", "price": None, "discount": None}

        caption = format_message(meta, link)
        image   = meta.get("image")

        for attempt in range(5):
            try:
                if image:
                    await bot.send_photo(chat_id=CHANNEL_ID, photo=image,
                                         caption=caption, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=CHANNEL_ID, text=caption,
                                           parse_mode="Markdown", disable_web_page_preview=False)
                logger.info(f"✅ Posted: {url}")
                return True

            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 2)
            except NetworkError as e:
                await asyncio.sleep(2 ** attempt)
            except TelegramError as e:
                if "Wrong file" in str(e) or "Invalid url" in str(e):
                    image = None   # retry as text
                else:
                    logger.error(f"Telegram error: {e}")
                    return False
        return False
    except Exception as e:
        logger.exception(f"send_deal error: {e}")
        return False

# ─────────────────────────────────────────────
# RSS AUTO-FETCH
# ─────────────────────────────────────────────
async def auto_fetch(bot: Bot):
    logger.info("🔄 RSS auto-fetch started…")
    count = 0
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                url = entry.get("link") or entry.get("id", "")
                if not url.startswith("http") or url in posted_urls:
                    continue
                skip = ["forum", "blog", "news", "category", "tag", "search"]
                if any(k in url.lower() for k in skip):
                    continue
                posted_urls.add(url)
                ok = await send_deal(bot, url)
                if ok:
                    count += 1
                    await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"RSS error ({feed_url}): {e}")
    logger.info(f"✅ Auto-fetch done. {count} deals posted.")

# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        "✅ *Deal Hunter Bot is LIVE!*\n\n"
        "📌 Commands:\n"
        "/post <url> — Post a deal manually\n"
        "/fetch — Trigger RSS fetch now\n"
        "/status — Bot health check",
        parse_mode="Markdown"
    )

async def cmd_post(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /post <url>")
        return
    url = context.args[0].strip()
    await update.message.reply_text(f"⏳ Processing…")
    ok = await send_deal(context.bot, url)
    await update.message.reply_text("✅ Posted!" if ok else "❌ Failed — check logs.")

async def cmd_fetch(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔄 Fetching RSS deals…")
    await auto_fetch(context.bot)
    await update.message.reply_text("✅ Done!")

async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        f"✅ *Bot Running*\n"
        f"📢 Channel: `{CHANNEL_ID}`\n"
        f"⏰ Auto-post: every `{AUTO_POST_INTERVAL}` min\n"
        f"🗂 URLs tracked: `{len(posted_urls)}`",
        parse_mode="Markdown"
    )

async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    urls = re.findall(r"https?://[^\s]+", update.message.text or "")
    for url in urls[:2]:
        await send_deal(context.bot, url)

# ─────────────────────────────────────────────
# MAIN  ← FIXED: proper PTB v21 async pattern
# ─────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set! Add it to environment variables.")
        return

    logger.info("🚀 Starting The Deal Hunter Bot…")

    # Build application — PTB v21 correct way
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("post",   cmd_post))
    app.add_handler(CommandHandler("fetch",  cmd_fetch))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Attach scheduler INSIDE post_init so event loop is ready
    async def post_init(application: Application):
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            auto_fetch,
            trigger="interval",
            minutes=AUTO_POST_INTERVAL,
            args=[application.bot],
            next_run_time=datetime.now() + timedelta(seconds=60),
        )
        scheduler.start()
        logger.info(f"⏰ Scheduler: auto-post every {AUTO_POST_INTERVAL} min")

    app.post_init = post_init

    # run_polling handles the event loop correctly in PTB v21
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
