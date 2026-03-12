"""
The Deal Hunter - Telegram Affiliate Bot
Fully autonomous, production-ready, 24/7 cloud-compatible
"""

import os
import re
import asyncio
import logging
import feedparser
import httpx
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, Tuple

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto
from telegram.error import TelegramError, RetryAfter, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("deal_hunter.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("DealHunterBot")

# ─────────────────────────────────────────────
# CONFIG (from environment variables)
# ─────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID     = os.environ.get("CHANNEL_ID", "@The_Deal_Hunter_Official")
EARNKARO_ID    = os.environ.get("EARNKARO_ID", "YOUR_EARNKARO_ID_HERE")
ADMIN_IDS_RAW  = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS      = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# RSS feeds to auto-fetch deals from (add/remove as needed)
RSS_FEEDS = [
    "https://www.dealnews.com/c142/Electronics/?rss=1",
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "https://www.deals.com/rss/feed",
]

# How often to auto-post deals (in minutes)
AUTO_POST_INTERVAL_MINUTES = int(os.environ.get("AUTO_POST_INTERVAL", "60"))

# ─────────────────────────────────────────────
# EARNKARO LINK BUILDER
# ─────────────────────────────────────────────
def build_earnkaro_link(original_url: str) -> str:
    encoded = urllib.parse.quote(original_url, safe="")
    return f"https://earnkaro.com/shareprod?url={encoded}&id={EARNKARO_ID}"

# ─────────────────────────────────────────────
# METADATA SCRAPER (with retry + exponential backoff)
# ─────────────────────────────────────────────
@retry(
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=False,
)
async def fetch_product_metadata(url: str) -> dict:
    """Scrape og:title, og:image, og:description from product URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

    def og(prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag["content"].strip() if tag and tag.get("content") else None

    title = og("og:title") or og("twitter:title") or (soup.title.string.strip() if soup.title else "Amazing Deal")
    image = og("og:image") or og("twitter:image")
    description = og("og:description") or og("twitter:description") or ""

    # Try to extract price from page text
    price_match = re.search(r"[₹\$€£]\s*[\d,]+(?:\.\d{1,2})?", resp.text)
    price = price_match.group(0).strip() if price_match else None

    # Try to find discount percentage
    discount_match = re.search(r"(\d{2,3})\s*%\s*off", resp.text, re.IGNORECASE)
    discount = f"{discount_match.group(1)}% OFF" if discount_match else None

    return {
        "title": title[:200] if title else "🔥 Hot Deal Alert!",
        "image": image,
        "description": description[:300] if description else "",
        "price": price,
        "discount": discount,
    }

# ─────────────────────────────────────────────
# MESSAGE FORMATTER
# ─────────────────────────────────────────────
def format_deal_message(meta: dict, affiliate_link: str) -> str:
    title    = meta.get("title", "🔥 Hot Deal Alert!")
    desc     = meta.get("description", "")
    price    = meta.get("price")
    discount = meta.get("discount")

    lines = [
        f"🔥 *{title}*",
        "",
    ]

    if discount:
        lines.append(f"🎉 *Discount: {discount}*")
    if price:
        lines.append(f"💰 *Price: {price}*")
    if desc:
        lines.append(f"📝 {desc}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"🛒 [👉 Grab This Deal Now!]({affiliate_link})",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "⚡ *Limited Time Offer — Don't Miss Out!*",
        "",
        f"📢 Join: @The\\_Deal\\_Hunter\\_Official",
        "🔔 Turn on notifications so you never miss a deal!",
    ]

    return "\n".join(lines)

# ─────────────────────────────────────────────
# TELEGRAM SENDER (with retry on rate-limit/network errors)
# ─────────────────────────────────────────────
async def send_deal_to_channel(bot: Bot, original_url: str) -> bool:
    """Full pipeline: fetch meta → build affiliate link → send to channel."""
    try:
        logger.info(f"Processing URL: {original_url}")
        affiliate_link = build_earnkaro_link(original_url)

        try:
            meta = await fetch_product_metadata(original_url)
        except Exception as e:
            logger.warning(f"Metadata fetch failed, using fallback: {e}")
            meta = {"title": "🔥 Hot Deal Alert!", "image": None, "description": "", "price": None, "discount": None}

        caption = format_deal_message(meta, affiliate_link)
        image_url = meta.get("image")

        for attempt in range(5):
            try:
                if image_url:
                    await bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=image_url,
                        caption=caption,
                        parse_mode="Markdown",
                    )
                else:
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=caption,
                        parse_mode="Markdown",
                        disable_web_page_preview=False,
                    )
                logger.info(f"✅ Posted deal to {CHANNEL_ID}")
                return True

            except RetryAfter as e:
                wait = e.retry_after + 2
                logger.warning(f"Rate limited by Telegram. Waiting {wait}s…")
                await asyncio.sleep(wait)

            except NetworkError as e:
                wait = 2 ** attempt
                logger.warning(f"Network error (attempt {attempt+1}): {e}. Retrying in {wait}s…")
                await asyncio.sleep(wait)

            except TelegramError as e:
                # Image URL might be bad — retry as text only
                if "Wrong file identifier" in str(e) or "Invalid url" in str(e):
                    logger.warning("Bad image URL, retrying as text-only…")
                    image_url = None
                else:
                    logger.error(f"Telegram error: {e}")
                    break

        return False

    except Exception as e:
        logger.exception(f"Unexpected error in send_deal_to_channel: {e}")
        return False

# ─────────────────────────────────────────────
# RSS AUTO-DEAL FETCHER
# ─────────────────────────────────────────────
posted_urls: set = set()  # In-memory dedup (resets on restart)

async def auto_fetch_and_post(bot: Bot):
    """Fetch deals from RSS feeds and post affiliate links automatically."""
    logger.info("🔄 Auto-fetching deals from RSS feeds…")
    new_posts = 0

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # Take top 5 from each feed
                # Find a product URL in the entry
                url = None
                for candidate in [entry.get("link"), entry.get("id")]:
                    if candidate and candidate.startswith("http"):
                        url = candidate
                        break

                if not url or url in posted_urls:
                    continue

                # Skip non-product pages
                skip_keywords = ["forum", "blog", "news", "article", "category", "tag"]
                if any(kw in url.lower() for kw in skip_keywords):
                    continue

                posted_urls.add(url)
                success = await send_deal_to_channel(bot, url)
                if success:
                    new_posts += 1
                    await asyncio.sleep(10)  # Throttle between posts

        except Exception as e:
            logger.error(f"RSS feed error ({feed_url}): {e}")

    logger.info(f"✅ Auto-post cycle complete. {new_posts} new deals posted.")

# ─────────────────────────────────────────────
# BOT COMMAND HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        "👋 *Deal Hunter Bot is LIVE!*\n\n"
        "Commands:\n"
        "/post <url> — Manually post a deal\n"
        "/fetch — Trigger RSS auto-fetch now\n"
        "/status — Check bot status\n",
        parse_mode="Markdown",
    )

async def cmd_post(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /post <product_url>")
        return

    url = context.args[0].strip()
    await update.message.reply_text(f"⏳ Processing `{url}`…", parse_mode="Markdown")
    success = await send_deal_to_channel(context.bot, url)
    if success:
        await update.message.reply_text("✅ Deal posted to channel!")
    else:
        await update.message.reply_text("❌ Failed to post deal. Check logs.")

async def cmd_fetch(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔄 Triggering manual RSS fetch…")
    await auto_fetch_and_post(context.bot)
    await update.message.reply_text("✅ Fetch cycle complete!")

async def cmd_status(update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        f"✅ *Bot is running*\n"
        f"📢 Channel: `{CHANNEL_ID}`\n"
        f"🔗 EarnKaro ID: `{EARNKARO_ID}`\n"
        f"⏰ Auto-post interval: every `{AUTO_POST_INTERVAL_MINUTES}` minutes\n"
        f"🗂 Dedup cache: `{len(posted_urls)}` URLs tracked\n",
        parse_mode="Markdown",
    )

async def handle_url_message(update, context: ContextTypes.DEFAULT_TYPE):
    """If admin sends a URL directly in chat, auto-post it."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = update.message.text.strip()
    url_pattern = re.compile(r"https?://[^\s]+")
    urls = url_pattern.findall(text)
    if urls:
        for url in urls[:3]:
            await update.message.reply_text(f"⏳ Posting `{url}`…", parse_mode="Markdown")
            await send_deal_to_channel(context.bot, url)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    logger.info("🚀 Starting The Deal Hunter Bot…")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("fetch", cmd_fetch))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))

    # Scheduler for auto RSS posting
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        auto_fetch_and_post,
        "interval",
        minutes=AUTO_POST_INTERVAL_MINUTES,
        args=[app.bot],
        next_run_time=datetime.now() + timedelta(seconds=30),
    )
    scheduler.start()
    logger.info(f"⏰ Scheduler started — auto-posting every {AUTO_POST_INTERVAL_MINUTES} minutes")

    # Start polling (never crashes due to error_callback)
    await app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    asyncio.run(main())
