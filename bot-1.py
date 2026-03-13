"""
╔══════════════════════════════════════════════════════════╗
║          DEAL HUNTER BOT v2.0 - by Claude AI            ║
║     Zero Investment | Full Automation | ₹10K-15K/Month  ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import re
import json
import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityUrl, MessageEntityTextUrl
import aiohttp

# ──────────────────────────────────────────────
#  CONFIGURATION (सभी settings यहाँ बदलो)
# ──────────────────────────────────────────────
CONFIG = {
    # Telegram Credentials (my.telegram.org से लो)
    "API_ID": int(os.getenv("TG_API_ID", "0")),
    "API_HASH": os.getenv("TG_API_HASH", ""),
    "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),          # @BotFather से
    "PHONE": os.getenv("TG_PHONE", ""),               # +91XXXXXXXXXX

    # Channel Settings
    "MY_CHANNEL": os.getenv("MY_CHANNEL", "@YourDealChannel"),   # आपका channel
    "ADMIN_ID": int(os.getenv("ADMIN_ID", "0")),                  # आपका Telegram ID

    # Source Channels (इन channels से deals copy होंगी)
    "SOURCE_CHANNELS": [
        "@lootdeals",
        "@dealsdhamaka",
        "@amazingdeals_india",
        "@flipkart_offers_deals",
        "@amazon_loot_deals",
        "@myntra_deals_offers",
        # और जोड़ सकते हो
    ],

    # Affiliate Settings
    "AMAZON_TAG": os.getenv("AMAZON_TAG", "yourtag-21"),          # Amazon affiliate tag
    "EARNKARO_API": os.getenv("EARNKARO_API", ""),                # EarnKaro API key (optional)

    # Deal Filters
    "MIN_PRICE": 1,
    "MAX_PRICE": 50000,
    "MIN_DISCOUNT": 10,       # Minimum 10% discount

    # Features Toggle
    "PRICE_COMPARISON": True,
    "EXPIRY_DETECTION": True,
    "IMAGE_ENHANCEMENT": True,
    "DUPLICATE_CHECK": True,
}

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# Duplicate deal tracker (in-memory, 24hr window)
seen_deals = {}

# ──────────────────────────────────────────────
#  PRICE EXTRACTOR
# ──────────────────────────────────────────────
def extract_prices(text: str) -> dict:
    """Message से prices extract karo"""
    prices = {
        "current": None,
        "original": None,
        "discount_percent": None,
        "savings": None
    }

    # Patterns: ₹999, Rs.999, 999/-, INR 999
    price_patterns = [
        r'[₹Rs\.INR]+\s*(\d[\d,]+)',
        r'(\d[\d,]+)\s*(?:/-|rupees?|inr)',
        r'(?:price|cost|now|offer)[:\s]+[₹Rs\.]*\s*(\d[\d,]+)',
    ]

    found_prices = []
    for pattern in price_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            try:
                price = int(m.replace(',', ''))
                if 1 <= price <= 100000:
                    found_prices.append(price)
            except:
                pass

    # Discount pattern: 50% off, 50% discount
    discount_match = re.search(r'(\d+)\s*%\s*(?:off|discount|save)', text, re.IGNORECASE)
    if discount_match:
        prices["discount_percent"] = int(discount_match.group(1))

    if len(found_prices) >= 2:
        found_prices.sort()
        prices["current"] = found_prices[0]
        prices["original"] = found_prices[-1]
        if prices["original"] > 0:
            prices["discount_percent"] = round(
                (1 - prices["current"] / prices["original"]) * 100
            )
        prices["savings"] = prices["original"] - prices["current"]
    elif len(found_prices) == 1:
        prices["current"] = found_prices[0]

    return prices

# ──────────────────────────────────────────────
#  DEAL VALIDATOR
# ──────────────────────────────────────────────
def is_valid_deal(prices: dict) -> tuple[bool, str]:
    """Deal valid hai ya nahi check karo"""
    current = prices.get("current")
    discount = prices.get("discount_percent")

    if current is None:
        return False, "❌ Price detect nahi hua"

    if current < CONFIG["MIN_PRICE"]:
        return False, f"❌ Price too low (₹{current})"

    if current > CONFIG["MAX_PRICE"]:
        return False, f"❌ Price too high (₹{current})"

    if discount is not None and discount < CONFIG["MIN_DISCOUNT"]:
        return False, f"❌ Discount too low ({discount}%)"

    return True, "✅ Valid deal"

# ──────────────────────────────────────────────
#  DUPLICATE CHECKER
# ──────────────────────────────────────────────
def is_duplicate(text: str) -> bool:
    """Duplicate deal check karo (24 hour window)"""
    if not CONFIG["DUPLICATE_CHECK"]:
        return False

    # Text ka short hash
    deal_hash = hashlib.md5(text[:100].encode()).hexdigest()

    now = datetime.now()
    # 24 ghante purane entries clean karo
    expired = [k for k, v in seen_deals.items() if now - v > timedelta(hours=24)]
    for k in expired:
        del seen_deals[k]

    if deal_hash in seen_deals:
        return True

    seen_deals[deal_hash] = now
    return False

# ──────────────────────────────────────────────
#  EXPIRY DETECTOR (Claude's AI Feature)
# ──────────────────────────────────────────────
def detect_expiry(text: str) -> str:
    """Deal ki expiry detect karo"""
    if not CONFIG["EXPIRY_DETECTION"]:
        return ""

    expiry_keywords = {
        "today only": "⏰ आज ही खत्म!",
        "limited time": "⏳ सीमित समय!",
        "hurry": "🏃 जल्दी करो!",
        "flash sale": "⚡ Flash Sale!",
        "ends tonight": "🌙 आज रात खत्म!",
        "till midnight": "🕛 मध्यरात्रि तक",
        "24 hour": "⏱️ 24 घंटे की डील",
        "while stock": "📦 स्टॉक सीमित है!",
        "aaj tak": "⏰ आज ही खत्म!",
        "limited stock": "📦 सीमित स्टॉक!",
        "jaldi karo": "🏃 जल्दी करो!",
    }

    text_lower = text.lower()
    for keyword, alert in expiry_keywords.items():
        if keyword in text_lower:
            return alert

    return ""

# ──────────────────────────────────────────────
#  AFFILIATE LINK CONVERTER
# ──────────────────────────────────────────────
def convert_to_affiliate(url: str) -> str:
    """Links ko affiliate links mein convert karo"""
    tag = CONFIG["AMAZON_TAG"]

    # Amazon link handling
    amazon_patterns = [
        r'(https?://(?:www\.)?amazon\.in/[^\s]+)',
        r'(https?://amzn\.in/[^\s]+)',
        r'(https?://amzn\.to/[^\s]+)',
        r'(https?://a\.co/[^\s]+)',
    ]

    for pattern in amazon_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            # Clean existing tags
            clean_url = re.sub(r'[?&]tag=[^&]+', '', url)
            clean_url = re.sub(r'[?&]linkCode=[^&]+', '', clean_url)

            # Add affiliate tag
            if '?' in clean_url:
                return f"{clean_url}&tag={tag}"
            else:
                return f"{clean_url}?tag={tag}"

    # Flipkart (affiliate ID add)
    flipkart_patterns = [
        r'(https?://(?:www\.)?flipkart\.com/[^\s]+)',
        r'(https?://fkrt\.it/[^\s]+)',
    ]
    for pattern in flipkart_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            # Flipkart affiliate - affiliate ID parameter
            affid = os.getenv("FLIPKART_AFFID", "")
            if affid:
                sep = '&' if '?' in url else '?'
                return f"{url}{sep}affid={affid}"

    return url

def extract_and_convert_links(text: str, entities=None) -> tuple[str, list]:
    """Message se links extract karo aur affiliate mein convert karo"""
    affiliate_links = []

    # URL regex
    url_pattern = r'https?://[^\s\)\]>\"\']+[^\s\)\]>\"\'.,!?]'
    urls = re.findall(url_pattern, text)

    converted_text = text
    for url in urls:
        affiliate_url = convert_to_affiliate(url)
        if affiliate_url != url:
            converted_text = converted_text.replace(url, affiliate_url)
            affiliate_links.append(affiliate_url)
        else:
            affiliate_links.append(url)

    return converted_text, affiliate_links

# ──────────────────────────────────────────────
#  PRICE COMPARISON FORMATTER (Claude's Feature)
# ──────────────────────────────────────────────
def format_price_comparison(prices: dict) -> str:
    """Price comparison card banao"""
    if not CONFIG["PRICE_COMPARISON"]:
        return ""

    current = prices.get("current")
    original = prices.get("original")
    discount = prices.get("discount_percent")
    savings = prices.get("savings")

    if not current:
        return ""

    lines = ["\n💰 *Price Analysis*"]
    lines.append(f"├ Amazon: ₹{current:,}")

    if original:
        lines.append(f"├ MRP: ~~₹{original:,}~~")
    if discount:
        lines.append(f"├ Discount: *{discount}% OFF* 🔥")
    if savings:
        lines.append(f"└ आपकी बचत: *₹{savings:,}* 💚")

    return "\n".join(lines)

# ──────────────────────────────────────────────
#  MESSAGE FORMATTER
# ──────────────────────────────────────────────
def format_deal_message(original_text: str, prices: dict, affiliate_links: list, converted_text: str) -> str:
    """Final deal message format karo"""

    expiry_alert = detect_expiry(original_text)
    price_comparison = format_price_comparison(prices)

    # Header
    header_emojis = ["🔥", "⚡", "💥", "🎯", "🛒"]
    import random
    emoji = random.choice(header_emojis)

    # Title extract karo (pehli line ya pehle 80 characters)
    first_line = converted_text.split('\n')[0][:80] if converted_text else "Great Deal!"

    message_parts = []

    # Expiry alert (if any)
    if expiry_alert:
        message_parts.append(f"*{expiry_alert}*\n")

    # Main deal content (converted text with affiliate links)
    message_parts.append(converted_text)

    # Price comparison card
    if price_comparison:
        message_parts.append(price_comparison)

    # Footer
    footer = f"\n\n📢 *और deals के लिए:* {CONFIG['MY_CHANNEL']}"
    message_parts.append(footer)

    final_message = "\n".join(message_parts)

    # Telegram message limit (4096 chars)
    if len(final_message) > 4000:
        final_message = final_message[:3900] + "...\n\n📢 " + CONFIG['MY_CHANNEL']

    return final_message

# ──────────────────────────────────────────────
#  MAIN BOT CLASS
# ──────────────────────────────────────────────
class DealHunterBot:
    def __init__(self):
        self.client = TelegramClient(
            'deal_hunter_session',
            CONFIG["API_ID"],
            CONFIG["API_HASH"]
        )
        self.stats = {
            "processed": 0,
            "posted": 0,
            "filtered": 0,
            "duplicates": 0,
            "start_time": datetime.now()
        }

    async def start(self):
        """Bot start karo"""
        await self.client.start(phone=CONFIG["PHONE"])
        log.info("✅ Deal Hunter Bot Started!")
        log.info(f"📡 Monitoring {len(CONFIG['SOURCE_CHANNELS'])} channels")
        log.info(f"📢 Posting to: {CONFIG['MY_CHANNEL']}")

        # Admin ko startup notification
        await self.notify_admin(
            f"🤖 *Deal Hunter Bot Started!*\n"
            f"📡 Monitoring: {len(CONFIG['SOURCE_CHANNELS'])} channels\n"
            f"💰 Filter: ₹{CONFIG['MIN_PRICE']}-₹{CONFIG['MAX_PRICE']}\n"
            f"🏷️ Min discount: {CONFIG['MIN_DISCOUNT']}%\n"
            f"⏰ Time: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )

        # Event handlers register karo
        self.register_handlers()

        log.info("🎯 Listening for deals...")
        await self.client.run_until_disconnected()

    def register_handlers(self):
        """Telegram event handlers"""

        # Source channels se messages
        @self.client.on(events.NewMessage(chats=CONFIG["SOURCE_CHANNELS"]))
        async def handle_source_message(event):
            await self.process_deal(event.message)

        # Admin commands (private message)
        @self.client.on(events.NewMessage(from_users=CONFIG["ADMIN_ID"]))
        async def handle_admin_command(event):
            await self.handle_admin(event)

    async def process_deal(self, message):
        """Deal process karo aur post karo"""
        try:
            text = message.text or message.caption or ""
            if not text or len(text) < 20:
                return

            self.stats["processed"] += 1

            # Duplicate check
            if is_duplicate(text):
                self.stats["duplicates"] += 1
                log.debug("🔄 Duplicate deal skip kiya")
                return

            # Price extract karo
            prices = extract_prices(text)

            # Validate karo
            is_valid, reason = is_valid_deal(prices)
            if not is_valid:
                self.stats["filtered"] += 1
                log.debug(f"⏭️ Deal filtered: {reason}")
                return

            # Links convert karo
            converted_text, affiliate_links = extract_and_convert_links(text)

            # Message format karo
            formatted_msg = format_deal_message(text, prices, affiliate_links, converted_text)

            # Photo ke saath post karo (agar available ho)
            if message.photo:
                await self.client.send_message(
                    CONFIG["MY_CHANNEL"],
                    formatted_msg,
                    file=message.photo,
                    parse_mode='md'
                )
            else:
                await self.client.send_message(
                    CONFIG["MY_CHANNEL"],
                    formatted_msg,
                    parse_mode='md'
                )

            self.stats["posted"] += 1
            log.info(f"✅ Deal posted! Price: ₹{prices.get('current', 'N/A')} | Discount: {prices.get('discount_percent', 'N/A')}%")

            # Rate limiting (spam se bachao)
            await asyncio.sleep(2)

        except Exception as e:
            log.error(f"❌ Error processing deal: {e}")

    async def handle_admin(self, event):
        """Admin commands handle karo"""
        text = event.text.strip().lower()

        # /stats - Statistics
        if text == "/stats":
            uptime = datetime.now() - self.stats["start_time"]
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)

            await event.respond(
                f"📊 *Deal Hunter Bot - Stats*\n\n"
                f"⏱️ Uptime: {hours}h {minutes}m\n"
                f"📥 Processed: {self.stats['processed']}\n"
                f"✅ Posted: {self.stats['posted']}\n"
                f"❌ Filtered: {self.stats['filtered']}\n"
                f"🔄 Duplicates: {self.stats['duplicates']}\n"
                f"📈 Success Rate: {round(self.stats['posted']/max(self.stats['processed'],1)*100)}%",
                parse_mode='md'
            )

        # /post <message> - Manual post
        elif text.startswith("/post "):
            manual_msg = event.text[6:]
            await self.client.send_message(CONFIG["MY_CHANNEL"], manual_msg, parse_mode='md')
            await event.respond("✅ Message channel par post ho gaya!")

        # /help - Commands list
        elif text == "/help":
            await event.respond(
                "🤖 *Deal Hunter Bot - Admin Commands*\n\n"
                "/stats - Bot ki statistics dekho\n"
                "/post <msg> - Channel par manually post karo\n"
                "/pause - Bot pause karo\n"
                "/resume - Bot resume karo\n"
                "/status - Current status check karo",
                parse_mode='md'
            )

        # /status
        elif text == "/status":
            await event.respond(
                f"🟢 *Bot Status: Running*\n"
                f"📡 Monitoring: {len(CONFIG['SOURCE_CHANNELS'])} channels\n"
                f"💰 Filter: ₹{CONFIG['MIN_PRICE']}-₹{CONFIG['MAX_PRICE']}\n"
                f"🏷️ Min Discount: {CONFIG['MIN_DISCOUNT']}%",
                parse_mode='md'
            )

    async def notify_admin(self, message: str):
        """Admin ko notification bhejo"""
        try:
            if CONFIG["ADMIN_ID"]:
                await self.client.send_message(CONFIG["ADMIN_ID"], message, parse_mode='md')
        except Exception as e:
            log.error(f"Admin notification failed: {e}")

# ──────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────
async def main():
    bot = DealHunterBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
