"""
╔══════════════════════════════════════════════════════════════╗
║         DEAL HUNTER BOT v3.0 - Double Engine Edition        ║
║   EarnKaro + Cuelinks | Smart Cleaner | Buttons | Auto-AI   ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import re
import os
import hashlib
import random
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.custom import Button
import aiohttp
from aiohttp import web

# ──────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────
CONFIG = {
    # Telegram Credentials
    "API_ID":    int(os.getenv("TG_API_ID", "0")),
    "API_HASH":  os.getenv("TG_API_HASH", ""),
    "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
    "PHONE":     os.getenv("TG_PHONE", ""),

    # Channel Settings
    "MY_CHANNEL": os.getenv("MY_CHANNEL", "@YourDealChannel"),
    "ADMIN_ID":   int(os.getenv("ADMIN_ID", "0")),

    # Source Channels
    "SOURCE_CHANNELS": [
        "@lootdeals", "@dealsdhamaka", "@amazingdeals_india",
        "@flipkart_offers_deals", "@amazon_loot_deals",
        "@myntra_deals_offers", "@meesho_loot_deals",
    ],

    # ── DOUBLE ENGINE AFFILIATE ──
    "AMAZON_TAG":      os.getenv("AMAZON_TAG", "yourtag-21"),
    "EARNKARO_TOKEN":  os.getenv("EARNKARO_TOKEN", ""),   # JWT token
    "CUELINKS_PID":    os.getenv("CUELINKS_PID", ""),     # Cuelinks Publisher ID
    "CUELINKS_KEY":    os.getenv("CUELINKS_KEY", ""),     # Cuelinks API Key
    "FLIPKART_AFFID":  os.getenv("FLIPKART_AFFID", ""),

    # Deal Filters
    "MIN_PRICE":    1,
    "MAX_PRICE":    50000,
    "MIN_DISCOUNT": 10,

    # Feature Toggles
    "USE_BUTTONS":        True,   # Inline buttons ke saath post
    "PRICE_COMPARISON":   True,
    "EXPIRY_DETECTION":   True,
    "DUPLICATE_CHECK":    True,
    "SMART_CLEANER":      True,   # Old channel names + junk fonts hatao
}

# ──────────────────────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

seen_deals = {}   # duplicate tracker

# ──────────────────────────────────────────────────────────────
#  SMART TEXT CLEANER  ← NEW FEATURE
# ──────────────────────────────────────────────────────────────

# Junk/fancy Unicode fonts → normal ASCII
UNICODE_FONT_MAP = {}
ranges = [
    (0x1D400, 0x1D419, 'A'),  # Bold capital
    (0x1D41A, 0x1D433, 'a'),  # Bold small
    (0x1D434, 0x1D44D, 'A'),  # Italic capital
    (0x1D44E, 0x1D467, 'a'),  # Italic small
    (0x1D468, 0x1D481, 'A'),  # Bold Italic capital
    (0x1D482, 0x1D49B, 'a'),  # Bold Italic small
    (0x1D4D0, 0x1D4E9, 'A'),  # Script capital
    (0x1D4EA, 0x1D503, 'a'),  # Script small
    (0x1D538, 0x1D551, 'A'),  # Double-struck capital
    (0x1D552, 0x1D56B, 'a'),  # Double-struck small
]
for start, end, base_char in ranges:
    base_ord = ord(base_char)
    for i, code in enumerate(range(start, end + 1)):
        UNICODE_FONT_MAP[chr(code)] = chr(base_ord + i)

# Numbers: bold/double-struck
for i in range(10):
    UNICODE_FONT_MAP[chr(0x1D7CE + i)] = str(i)   # Bold digits
    UNICODE_FONT_MAP[chr(0x1D7D8 + i)] = str(i)   # Double-struck digits

def clean_fancy_fonts(text: str) -> str:
    """Fancy Unicode fonts ko normal text mein convert karo"""
    return ''.join(UNICODE_FONT_MAP.get(c, c) for c in text)

# Channel username patterns to remove
CHANNEL_PATTERNS = [
    r'@[A-Za-z0-9_]{3,32}',                          # @channelname
    r'(?:join|t\.me|telegram\.me)/[A-Za-z0-9_/+]+',  # t.me/channel
    r'(?:channel|group|join us|join now)[^\n]*\n?',   # "Join our channel"
    r'(?:forwarded from|shared from)[^\n]*\n?',       # Forward headers
    r'━+|─+|▬+|•{3,}',                               # Decorative lines
    r'[\u2500-\u257F]{2,}',                           # Box drawing chars
]

def clean_channel_refs(text: str) -> str:
    """Old channel names aur promotional text hatao"""
    if not CONFIG["SMART_CLEANER"]:
        return text
    for pattern in CHANNEL_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    # Multiple blank lines → single
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def clean_message(text: str) -> str:
    """Full clean pipeline"""
    text = clean_fancy_fonts(text)
    text = clean_channel_refs(text)
    return text

# ──────────────────────────────────────────────────────────────
#  PLATFORM DETECTOR (Auto-Detect which affiliate to use)
# ──────────────────────────────────────────────────────────────

PLATFORM_RULES = {
    "amazon": {
        "patterns": [r'amazon\.in', r'amzn\.in', r'amzn\.to', r'a\.co/'],
        "engine": "earnkaro",      # EarnKaro gives best Amazon commission
        "fallback": "direct_tag",
    },
    "flipkart": {
        "patterns": [r'flipkart\.com', r'fkrt\.it'],
        "engine": "earnkaro",
        "fallback": "cuelinks",
    },
    "meesho": {
        "patterns": [r'meesho\.com'],
        "engine": "earnkaro",
        "fallback": "cuelinks",
    },
    "myntra": {
        "patterns": [r'myntra\.com'],
        "engine": "cuelinks",
        "fallback": "earnkaro",
    },
    "nykaa": {
        "patterns": [r'nykaa\.com', r'nykaafashion\.com'],
        "engine": "cuelinks",
        "fallback": "earnkaro",
    },
    "ajio": {
        "patterns": [r'ajio\.com'],
        "engine": "cuelinks",
        "fallback": "earnkaro",
    },
    "swiggy": {
        "patterns": [r'swiggy\.com'],
        "engine": "cuelinks",
        "fallback": None,
    },
    "zomato": {
        "patterns": [r'zomato\.com'],
        "engine": "cuelinks",
        "fallback": None,
    },
}

def detect_platform(url: str) -> tuple[str, str, str]:
    """URL se platform detect karo, return (platform, engine, fallback)"""
    url_lower = url.lower()
    for platform, rules in PLATFORM_RULES.items():
        for pattern in rules["patterns"]:
            if re.search(pattern, url_lower):
                return platform, rules["engine"], rules.get("fallback", "cuelinks")
    return "other", "cuelinks", None

# ──────────────────────────────────────────────────────────────
#  EARNKARO API  ← DOUBLE ENGINE: Engine 1
# ──────────────────────────────────────────────────────────────
async def earnkaro_convert(session: aiohttp.ClientSession, url: str) -> str | None:
    """EarnKaro API se affiliate link banao"""
    token = CONFIG["EARNKARO_TOKEN"]
    if not token:
        return None
    try:
        async with session.post(
            "https://api.earnkaro.com/create-link",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"url": url},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                link = data.get("data", {}).get("short_url") or data.get("short_url")
                if link:
                    log.info(f"✅ EarnKaro: {url[:40]}... → {link}")
                    return link
    except Exception as e:
        log.warning(f"EarnKaro API error: {e}")
    return None

# ──────────────────────────────────────────────────────────────
#  CUELINKS API  ← DOUBLE ENGINE: Engine 2
# ──────────────────────────────────────────────────────────────
async def cuelinks_convert(session: aiohttp.ClientSession, url: str) -> str | None:
    """Cuelinks API se affiliate link banao"""
    pid = CONFIG["CUELINKS_PID"]
    key = CONFIG["CUELINKS_KEY"]
    if not pid or not key:
        return None
    try:
        async with session.get(
            "https://api.cuelinks.com/v1/link",
            params={"pid": pid, "key": key, "url": url},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                link = data.get("data", {}).get("url") or data.get("url")
                if link:
                    log.info(f"✅ Cuelinks: {url[:40]}... → {link}")
                    return link
    except Exception as e:
        log.warning(f"Cuelinks API error: {e}")
    return None

def amazon_direct_tag(url: str) -> str:
    """Amazon tag directly add karo (no API needed)"""
    tag = CONFIG["AMAZON_TAG"]
    clean = re.sub(r'[?&]tag=[^&]+', '', url)
    clean = re.sub(r'[?&]linkCode=[^&]+', '', clean)
    sep = '&' if '?' in clean else '?'
    return f"{clean}{sep}tag={tag}"

# ──────────────────────────────────────────────────────────────
#  SMART LINK CONVERTER (Auto-Detect + Double Engine)
# ──────────────────────────────────────────────────────────────
async def smart_convert_url(session: aiohttp.ClientSession, url: str) -> tuple[str, str]:
    """
    URL ko best affiliate link mein convert karo.
    Returns: (converted_url, platform_name)
    """
    platform, primary_engine, fallback_engine = detect_platform(url)
    converted = None

    # Primary engine try karo
    if primary_engine == "earnkaro":
        converted = await earnkaro_convert(session, url)
    elif primary_engine == "cuelinks":
        converted = await cuelinks_convert(session, url)

    # Fallback engine try karo
    if not converted and fallback_engine:
        if fallback_engine == "earnkaro":
            converted = await earnkaro_convert(session, url)
        elif fallback_engine == "cuelinks":
            converted = await cuelinks_convert(session, url)
        elif fallback_engine == "direct_tag" and platform == "amazon":
            converted = amazon_direct_tag(url)

    # Amazon ke liye last resort: direct tag
    if not converted and platform == "amazon":
        converted = amazon_direct_tag(url)

    return (converted or url), platform

async def convert_all_links(session: aiohttp.ClientSession, text: str) -> tuple[str, list[dict]]:
    """Message ke saare links convert karo"""
    url_pattern = r'https?://[^\s\)\]>\"\'<]+[^\s\)\]>\"\'<.,!?]'
    urls = list(set(re.findall(url_pattern, text)))

    converted_text = text
    link_info = []

    for url in urls:
        new_url, platform = await smart_convert_url(session, url)
        if new_url != url:
            converted_text = converted_text.replace(url, new_url)
        link_info.append({
            "original": url,
            "converted": new_url,
            "platform": platform,
            "changed": new_url != url
        })

    return converted_text, link_info

# ──────────────────────────────────────────────────────────────
#  PRICE EXTRACTOR
# ──────────────────────────────────────────────────────────────
def extract_prices(text: str) -> dict:
    prices = {"current": None, "original": None, "discount_percent": None, "savings": None}
    price_patterns = [
        r'[₹Rs\.INR]+\s*(\d[\d,]+)',
        r'(\d[\d,]+)\s*(?:/-|rupees?|inr)',
        r'(?:price|cost|now|offer)[:\s]+[₹Rs\.]*\s*(\d[\d,]+)',
    ]
    found = []
    for p in price_patterns:
        for m in re.findall(p, text, re.IGNORECASE):
            try:
                v = int(m.replace(',', ''))
                if 1 <= v <= 100000:
                    found.append(v)
            except:
                pass

    disc = re.search(r'(\d+)\s*%\s*(?:off|discount|save)', text, re.IGNORECASE)
    if disc:
        prices["discount_percent"] = int(disc.group(1))

    if len(found) >= 2:
        found.sort()
        prices["current"] = found[0]
        prices["original"] = found[-1]
        prices["discount_percent"] = round((1 - found[0] / found[-1]) * 100)
        prices["savings"] = found[-1] - found[0]
    elif found:
        prices["current"] = found[0]

    return prices

def is_valid_deal(prices: dict) -> tuple[bool, str]:
    cur = prices.get("current")
    disc = prices.get("discount_percent")
    if cur is None:
        return False, "Price detect nahi hua"
    if cur < CONFIG["MIN_PRICE"] or cur > CONFIG["MAX_PRICE"]:
        return False, f"Price out of range (₹{cur})"
    if disc is not None and disc < CONFIG["MIN_DISCOUNT"]:
        return False, f"Discount too low ({disc}%)"
    return True, "OK"

def is_duplicate(text: str) -> bool:
    if not CONFIG["DUPLICATE_CHECK"]:
        return False
    h = hashlib.md5(text[:100].encode()).hexdigest()
    now = datetime.now()
    for k in [k for k, v in seen_deals.items() if now - v > timedelta(hours=24)]:
        del seen_deals[k]
    if h in seen_deals:
        return True
    seen_deals[h] = now
    return False

def detect_expiry(text: str) -> str:
    if not CONFIG["EXPIRY_DETECTION"]:
        return ""
    kw = {
        "today only": "⏰ आज ही खत्म!", "limited time": "⏳ सीमित समय!",
        "hurry": "🏃 जल्दी करो!", "flash sale": "⚡ Flash Sale!",
        "ends tonight": "🌙 आज रात खत्म!", "while stock": "📦 सीमित स्टॉक!",
        "limited stock": "📦 सीमित स्टॉक!", "aaj tak": "⏰ आज ही खत्म!",
        "jaldi": "🏃 जल्दी करो!", "24 hour": "⏱️ 24 घंटे की डील",
    }
    tl = text.lower()
    for k, v in kw.items():
        if k in tl:
            return v
    return ""

# ──────────────────────────────────────────────────────────────
#  BUTTON BUILDER  ← NEW FEATURE
# ──────────────────────────────────────────────────────────────
def build_buttons(link_info: list[dict], prices: dict) -> list | None:
    """Inline buttons banao deal links ke liye"""
    if not CONFIG["USE_BUTTONS"]:
        return None

    PLATFORM_LABELS = {
        "amazon":   "🛒 Amazon पर खरीदो",
        "flipkart": "🛍️ Flipkart पर खरीदो",
        "meesho":   "👗 Meesho पर खरीदो",
        "myntra":   "👠 Myntra पर खरीदो",
        "nykaa":    "💄 Nykaa पर खरीदो",
        "ajio":     "🧥 AJIO पर खरीदो",
        "swiggy":   "🍔 Swiggy पर खरीदो",
        "zomato":   "🍕 Zomato पर खरीदो",
        "other":    "🔗 Deal देखो",
    }

    buttons = []
    seen_platforms = set()

    for info in link_info:
        if not info["converted"]:
            continue
        platform = info["platform"]
        if platform in seen_platforms:
            continue
        seen_platforms.add(platform)

        label = PLATFORM_LABELS.get(platform, "🔗 Deal देखो")

        # Price label add (agar available ho)
        cur = prices.get("current")
        if cur and platform in ("amazon", "flipkart", "meesho"):
            label += f" @ ₹{cur:,}"

        buttons.append([Button.url(label, info["converted"])])

    # Share button
    if buttons:
        buttons.append([Button.url("📢 Channel Join करो", f"https://t.me/{CONFIG['MY_CHANNEL'].lstrip('@')}")])

    return buttons if buttons else None

# ──────────────────────────────────────────────────────────────
#  MESSAGE FORMATTER
# ──────────────────────────────────────────────────────────────
def format_deal_message(cleaned_text: str, prices: dict, link_info: list) -> str:
    expiry = detect_expiry(cleaned_text)
    parts = []

    if expiry:
        parts.append(f"*{expiry}*\n")

    # Main content (links already converted inside cleaned_text)
    # Remove raw URLs from text if buttons are enabled (cleaner look)
    display_text = cleaned_text
    if CONFIG["USE_BUTTONS"]:
        url_pattern = r'https?://[^\s\)\]>\"\'<]+[^\s\)\]>\"\'<.,!?]'
        display_text = re.sub(url_pattern, '', display_text).strip()
        display_text = re.sub(r'\n{3,}', '\n\n', display_text)

    parts.append(display_text)

    # Price analysis card
    cur = prices.get("current")
    orig = prices.get("original")
    disc = prices.get("discount_percent")
    save = prices.get("savings")

    if cur and CONFIG["PRICE_COMPARISON"]:
        card = ["\n💰 *Price Analysis*"]
        card.append(f"├ Current Price: ₹{cur:,}")
        if orig:
            card.append(f"├ MRP: ~~₹{orig:,}~~")
        if disc:
            card.append(f"├ Discount: *{disc}% OFF* 🔥")
        if save:
            card.append(f"└ आपकी बचत: *₹{save:,}* 💚")
        parts.append("\n".join(card))

    # Affiliate engine tag (small, at bottom)
    engines_used = set()
    for info in link_info:
        if info["changed"]:
            engines_used.add(info["platform"].title())
    if engines_used:
        parts.append(f"\n🔗 _{', '.join(sorted(engines_used))} affiliate link_")

    msg = "\n".join(parts)
    if len(msg) > 4000:
        msg = msg[:3900] + "..."
    return msg

# ──────────────────────────────────────────────────────────────
#  MAIN BOT
# ──────────────────────────────────────────────────────────────
class DealHunterBot:
    def __init__(self):
        self.client = TelegramClient(
            'deal_hunter_session',
            CONFIG["API_ID"],
            CONFIG["API_HASH"]
        )
        self.stats = {
            "processed": 0, "posted": 0,
            "filtered": 0, "duplicates": 0,
            "earnkaro_conversions": 0,
            "cuelinks_conversions": 0,
            "start_time": datetime.now()
        }
        self.paused = False
        self.http: aiohttp.ClientSession | None = None

    async def start(self):
        self.http = aiohttp.ClientSession()
        await self.client.start(phone=CONFIG["PHONE"])
        log.info("✅ Deal Hunter Bot v3.0 Started!")

        engines = []
        if CONFIG["EARNKARO_TOKEN"]:
            engines.append("EarnKaro ✅")
        if CONFIG["CUELINKS_PID"]:
            engines.append("Cuelinks ✅")
        if not engines:
            engines.append("Amazon Direct Tag")

        await self.notify_admin(
            f"🤖 *Deal Hunter Bot v3.0 Started!*\n\n"
            f"⚡ Engines: {' | '.join(engines)}\n"
            f"📡 Monitoring: {len(CONFIG['SOURCE_CHANNELS'])} channels\n"
            f"💰 Filter: ₹{CONFIG['MIN_PRICE']}–₹{CONFIG['MAX_PRICE']}\n"
            f"🏷️ Min Discount: {CONFIG['MIN_DISCOUNT']}%\n"
            f"🔘 Buttons: {'ON' if CONFIG['USE_BUTTONS'] else 'OFF'}\n"
            f"🧹 Smart Cleaner: {'ON' if CONFIG['SMART_CLEANER'] else 'OFF'}\n"
            f"⏰ {datetime.now().strftime('%d/%m %H:%M')}"
        )

        self.register_handlers()
        log.info("🎯 Listening for deals...")
        await self.client.run_until_disconnected()

    def register_handlers(self):
        @self.client.on(events.NewMessage(chats=CONFIG["SOURCE_CHANNELS"]))
        async def on_deal(event):
            if not self.paused:
                await self.process_deal(event.message)

        @self.client.on(events.NewMessage(from_users=CONFIG["ADMIN_ID"]))
        async def on_admin(event):
            await self.handle_admin(event)

    async def process_deal(self, message):
        try:
            raw_text = message.text or message.caption or ""
            if not raw_text or len(raw_text) < 20:
                return

            self.stats["processed"] += 1

            # 1. Duplicate check
            if is_duplicate(raw_text):
                self.stats["duplicates"] += 1
                return

            # 2. Extract prices
            prices = extract_prices(raw_text)

            # 3. Validate deal
            ok, reason = is_valid_deal(prices)
            if not ok:
                self.stats["filtered"] += 1
                log.debug(f"⏭️ Filtered: {reason}")
                return

            # 4. Clean message (fonts + channel refs)
            cleaned = clean_message(raw_text)

            # 5. Convert links (Double Engine)
            converted_text, link_info = await convert_all_links(self.http, cleaned)

            # Count conversions
            for info in link_info:
                if info["changed"]:
                    if info["platform"] in ("amazon", "flipkart", "meesho"):
                        self.stats["earnkaro_conversions"] += 1
                    else:
                        self.stats["cuelinks_conversions"] += 1

            # 6. Format final message
            final_msg = format_deal_message(converted_text, prices, link_info)

            # 7. Build buttons
            buttons = build_buttons(link_info, prices)

            # 8. Post to channel
            kwargs = {"parse_mode": "md"}
            if buttons:
                kwargs["buttons"] = buttons

            if message.photo:
                await self.client.send_message(
                    CONFIG["MY_CHANNEL"], final_msg,
                    file=message.photo, **kwargs
                )
            else:
                await self.client.send_message(
                    CONFIG["MY_CHANNEL"], final_msg, **kwargs
                )

            self.stats["posted"] += 1
            log.info(
                f"✅ Posted | ₹{prices.get('current','?')} | "
                f"{prices.get('discount_percent','?')}% off | "
                f"Links: {sum(1 for i in link_info if i['changed'])}"
            )

            await asyncio.sleep(2)   # rate limit

        except Exception as e:
            log.error(f"❌ process_deal error: {e}", exc_info=True)

    async def handle_admin(self, event):
        cmd = event.text.strip().lower()

        if cmd == "/stats":
            up = datetime.now() - self.stats["start_time"]
            h, m = divmod(int(up.total_seconds()), 3600)
            m //= 60
            total = max(self.stats["processed"], 1)
            await event.respond(
                f"📊 *Deal Hunter Bot v3.0 — Stats*\n\n"
                f"⏱️ Uptime: {h}h {m}m\n"
                f"📥 Processed: {self.stats['processed']}\n"
                f"✅ Posted: {self.stats['posted']}\n"
                f"❌ Filtered: {self.stats['filtered']}\n"
                f"🔄 Duplicates: {self.stats['duplicates']}\n"
                f"🔗 EarnKaro links: {self.stats['earnkaro_conversions']}\n"
                f"🔗 Cuelinks links: {self.stats['cuelinks_conversions']}\n"
                f"📈 Success Rate: {round(self.stats['posted']/total*100)}%",
                parse_mode='md'
            )

        elif cmd.startswith("/post "):
            msg = event.text[6:]
            await self.client.send_message(CONFIG["MY_CHANNEL"], msg, parse_mode='md')
            await event.respond("✅ Posted!")

        elif cmd == "/pause":
            self.paused = True
            await event.respond("⏸️ Bot paused.")

        elif cmd == "/resume":
            self.paused = False
            await event.respond("▶️ Bot resumed!")

        elif cmd == "/buttons on":
            CONFIG["USE_BUTTONS"] = True
            await event.respond("🔘 Buttons: ON")

        elif cmd == "/buttons off":
            CONFIG["USE_BUTTONS"] = False
            await event.respond("🔘 Buttons: OFF")

        elif cmd == "/cleaner on":
            CONFIG["SMART_CLEANER"] = True
            await event.respond("🧹 Smart Cleaner: ON")

        elif cmd == "/cleaner off":
            CONFIG["SMART_CLEANER"] = False
            await event.respond("🧹 Smart Cleaner: OFF")

        elif cmd == "/status":
            await event.respond(
                f"{'⏸️ PAUSED' if self.paused else '🟢 Running'}\n"
                f"⚡ EarnKaro: {'✅' if CONFIG['EARNKARO_TOKEN'] else '❌'}\n"
                f"⚡ Cuelinks: {'✅' if CONFIG['CUELINKS_PID'] else '❌'}\n"
                f"🔘 Buttons: {'ON' if CONFIG['USE_BUTTONS'] else 'OFF'}\n"
                f"🧹 Cleaner: {'ON' if CONFIG['SMART_CLEANER'] else 'OFF'}",
                parse_mode='md'
            )

        elif cmd == "/help":
            await event.respond(
                "🤖 *Admin Commands v3.0*\n\n"
                "/stats — Statistics\n"
                "/status — Bot status\n"
                "/post <msg> — Manual post\n"
                "/pause — Bot pause\n"
                "/resume — Bot resume\n"
                "/buttons on|off — Toggle buttons\n"
                "/cleaner on|off — Toggle smart cleaner\n"
                "/help — Yeh list",
                parse_mode='md'
            )

    async def notify_admin(self, msg: str):
        try:
            if CONFIG["ADMIN_ID"]:
                await self.client.send_message(CONFIG["ADMIN_ID"], msg, parse_mode='md')
        except Exception as e:
            log.error(f"Admin notify failed: {e}")

# ──────────────────────────────────────────────────────────────
async def health(request):
    return web.Response(text="Bot is Alive!")

async def main():
    # Render के लिए पोर्ट सेटअप
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # पोर्ट 10000 का इस्तेमाल
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    # आपका असली बॉट शुरू करना
    bot = DealHunterBot()
    await bot.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
