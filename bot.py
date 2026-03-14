"""
╔══════════════════════════════════════════════════════════════╗
║         DEAL HUNTER BOT v3.1 - Render Edition               ║
║   EarnKaro + Cuelinks | Bot Token | Health Check Server     ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import re
import os
import hashlib
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from aiohttp import web
import aiohttp

# ──────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────
CONFIG = {
    "API_ID":    int(os.getenv("TG_API_ID", "0")),
    "API_HASH":  os.getenv("TG_API_HASH", ""),
    "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),

    "MY_CHANNEL": os.getenv("MY_CHANNEL", ""),
    "ADMIN_ID":   int(os.getenv("ADMIN_ID", "0")),

    # SOURCE_CHANNELS env mein comma-separated daalo:
    # e.g. "-1001234567890,@dealsdhamaka,-1009876543210"
    "SOURCE_CHANNELS": [
        (int(ch.strip()) if ch.strip().lstrip('-').isdigit() else ch.strip())
        for ch in os.getenv("SOURCE_CHANNELS", "").split(",")
        if ch.strip()
    ],

    # Affiliate Engines
    "EARNKARO_TOKEN": os.getenv("EARNKARO_TOKEN", ""),
    "CUELINKS_PID":   os.getenv("CUELINKS_PID", ""),
    "CUELINKS_KEY":   os.getenv("CUELINKS_KEY", ""),

    # Deal Filters
    "MIN_PRICE":    1,
    "MAX_PRICE":    50000,
    "MIN_DISCOUNT": 10,

    # Features
    "USE_BUTTONS":      True,
    "PRICE_COMPARISON": True,
    "EXPIRY_DETECTION": True,
    "DUPLICATE_CHECK":  True,
    "SMART_CLEANER":    True,

    # Render web server port
    "PORT": int(os.getenv("PORT", "10000")),
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
seen_deals = {}

# ──────────────────────────────────────────────────────────────
#  HEALTH CHECK WEB SERVER (Render ke liye zaroori)
# ──────────────────────────────────────────────────────────────
async def health_check(request):
    return web.Response(text="OK", content_type="text/plain")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CONFIG["PORT"]).start()
    log.info(f"Web server started on port {CONFIG['PORT']}")

# ──────────────────────────────────────────────────────────────
#  SMART TEXT CLEANER
# ──────────────────────────────────────────────────────────────
UNICODE_FONT_MAP = {}
for _s, _e, _b in [
    (0x1D400,0x1D419,'A'),(0x1D41A,0x1D433,'a'),(0x1D434,0x1D44D,'A'),
    (0x1D44E,0x1D467,'a'),(0x1D468,0x1D481,'A'),(0x1D482,0x1D49B,'a'),
    (0x1D4D0,0x1D4E9,'A'),(0x1D4EA,0x1D503,'a'),(0x1D538,0x1D551,'A'),
    (0x1D552,0x1D56B,'a'),
]:
    for _i,_c in enumerate(range(_s,_e+1)):
        UNICODE_FONT_MAP[chr(_c)] = chr(ord(_b)+_i)
for _i in range(10):
    UNICODE_FONT_MAP[chr(0x1D7CE+_i)] = str(_i)
    UNICODE_FONT_MAP[chr(0x1D7D8+_i)] = str(_i)

CHANNEL_PATTERNS = [
    r'@[A-Za-z0-9_]{3,32}',
    r'(?:join|t\.me|telegram\.me)/[A-Za-z0-9_/+]+',
    r'(?:channel|group|join us|join now)[^\n]*\n?',
    r'(?:forwarded from|shared from)[^\n]*\n?',
    r'━+|─+|▬+|•{3,}',
    r'[\u2500-\u257F]{2,}',
]

def clean_message(text: str) -> str:
    text = ''.join(UNICODE_FONT_MAP.get(c, c) for c in text)
    if CONFIG["SMART_CLEANER"]:
        for p in CHANNEL_PATTERNS:
            text = re.sub(p, '', text, flags=re.IGNORECASE)
        text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ──────────────────────────────────────────────────────────────
#  PLATFORM DETECTOR
# ──────────────────────────────────────────────────────────────
PLATFORM_RULES = {
    "flipkart": {"patterns": [r'flipkart\.com', r'fkrt\.it'],      "engine": "earnkaro", "fallback": "cuelinks"},
    "meesho":   {"patterns": [r'meesho\.com'],                      "engine": "earnkaro", "fallback": "cuelinks"},
    "myntra":   {"patterns": [r'myntra\.com'],                      "engine": "cuelinks", "fallback": "earnkaro"},
    "nykaa":    {"patterns": [r'nykaa\.com', r'nykaafashion\.com'], "engine": "cuelinks", "fallback": "earnkaro"},
    "ajio":     {"patterns": [r'ajio\.com'],                        "engine": "cuelinks", "fallback": "earnkaro"},
    "swiggy":   {"patterns": [r'swiggy\.com'],                      "engine": "cuelinks", "fallback": None},
    "zomato":   {"patterns": [r'zomato\.com'],                      "engine": "cuelinks", "fallback": None},
}

def detect_platform(url: str) -> tuple:
    for platform, rules in PLATFORM_RULES.items():
        for pattern in rules["patterns"]:
            if re.search(pattern, url.lower()):
                return platform, rules["engine"], rules.get("fallback")
    return "other", "cuelinks", None

# ──────────────────────────────────────────────────────────────
#  AFFILIATE ENGINES
# ──────────────────────────────────────────────────────────────
async def earnkaro_convert(session: aiohttp.ClientSession, url: str):
    token = CONFIG["EARNKARO_TOKEN"]
    if not token:
        return None
    try:
        async with session.post(
            "https://api.earnkaro.com/create-link",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"url": url},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", {}).get("short_url") or data.get("short_url")
    except Exception as e:
        log.warning(f"EarnKaro: {e}")
    return None

async def cuelinks_convert(session: aiohttp.ClientSession, url: str):
    pid, key = CONFIG["CUELINKS_PID"], CONFIG["CUELINKS_KEY"]
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
                return data.get("data", {}).get("url") or data.get("url")
    except Exception as e:
        log.warning(f"Cuelinks: {e}")
    return None

async def smart_convert(session, url):
    platform, primary, fallback = detect_platform(url)
    converted = None
    if primary == "earnkaro":
        converted = await earnkaro_convert(session, url)
    elif primary == "cuelinks":
        converted = await cuelinks_convert(session, url)
    if not converted and fallback:
        if fallback == "earnkaro":
            converted = await earnkaro_convert(session, url)
        elif fallback == "cuelinks":
            converted = await cuelinks_convert(session, url)
    return (converted or url), platform

async def convert_all_links(session, text):
    urls = list(set(re.findall(r'https?://[^\s\)\]>\"\'<]+[^\s\)\]>\"\'<.,!?]', text)))
    link_info = []
    for url in urls:
        new_url, platform = await smart_convert(session, url)
        if new_url != url:
            text = text.replace(url, new_url)
        link_info.append({"original": url, "converted": new_url, "platform": platform, "changed": new_url != url})
    return text, link_info

# ──────────────────────────────────────────────────────────────
#  PRICE EXTRACTOR & VALIDATORS
# ──────────────────────────────────────────────────────────────
def extract_prices(text):
    prices = {"current": None, "original": None, "discount_percent": None, "savings": None}
    found = []
    for p in [r'[₹Rs\.INR]+\s*(\d[\d,]+)', r'(\d[\d,]+)\s*(?:/-|rupees?|inr)']:
        for m in re.findall(p, text, re.IGNORECASE):
            try:
                v = int(m.replace(',', ''))
                if 1 <= v <= 100000:
                    found.append(v)
            except: pass
    disc = re.search(r'(\d+)\s*%\s*(?:off|discount|save)', text, re.IGNORECASE)
    if disc:
        prices["discount_percent"] = int(disc.group(1))
    if len(found) >= 2:
        found.sort()
        prices.update({"current": found[0], "original": found[-1],
                        "discount_percent": round((1-found[0]/found[-1])*100),
                        "savings": found[-1]-found[0]})
    elif found:
        prices["current"] = found[0]
    return prices

def is_valid_deal(prices):
    cur, disc = prices.get("current"), prices.get("discount_percent")
    if cur is None: return False, "Price nahi mila"
    if not (CONFIG["MIN_PRICE"] <= cur <= CONFIG["MAX_PRICE"]): return False, f"Range se bahar"
    if disc is not None and disc < CONFIG["MIN_DISCOUNT"]: return False, f"Discount kam"
    return True, "OK"

def is_duplicate(text):
    if not CONFIG["DUPLICATE_CHECK"]: return False
    h = hashlib.md5(text[:100].encode()).hexdigest()
    now = datetime.now()
    for k in [k for k,v in seen_deals.items() if now-v > timedelta(hours=24)]:
        del seen_deals[k]
    if h in seen_deals: return True
    seen_deals[h] = now
    return False

def detect_expiry(text):
    if not CONFIG["EXPIRY_DETECTION"]: return ""
    for k,v in {"today only":"⏰ आज ही खत्म!","flash sale":"⚡ Flash Sale!",
                "limited stock":"📦 सीमित स्टॉक!","while stock":"📦 सीमित स्टॉक!",
                "jaldi":"🏃 जल्दी करो!","24 hour":"⏱️ 24 घंटे"}.items():
        if k in text.lower(): return v
    return ""

# ──────────────────────────────────────────────────────────────
#  BUTTON BUILDER
# ──────────────────────────────────────────────────────────────
PLABELS = {
    "flipkart":"🛍️ Flipkart पर खरीदो","meesho":"👗 Meesho पर खरीदो",
    "myntra":"👠 Myntra पर खरीदो","nykaa":"💄 Nykaa पर खरीदो",
    "ajio":"🧥 AJIO पर खरीदो","swiggy":"🍔 Swiggy","zomato":"🍕 Zomato","other":"🔗 Deal देखो",
}

def build_buttons(link_info, prices):
    if not CONFIG["USE_BUTTONS"]: return None
    buttons, seen = [], set()
    for info in link_info:
        if not info["converted"] or info["platform"] in seen: continue
        seen.add(info["platform"])
        label = PLABELS.get(info["platform"], "🔗 Deal देखो")
        cur = prices.get("current")
        if cur: label += f" @ ₹{cur:,}"
        buttons.append([Button.url(label, info["converted"])])
    if buttons:
        buttons.append([Button.url("📢 Channel Join करो", f"https://t.me/{CONFIG['MY_CHANNEL'].lstrip('@')}")])
    return buttons or None

# ──────────────────────────────────────────────────────────────
#  MESSAGE FORMATTER
# ──────────────────────────────────────────────────────────────
def format_message(cleaned_text, prices, link_info):
    parts = []
    expiry = detect_expiry(cleaned_text)
    if expiry: parts.append(f"*{expiry}*\n")

    display = cleaned_text
    if CONFIG["USE_BUTTONS"]:
        display = re.sub(r'https?://[^\s\)\]>\"\'<]+[^\s\)\]>\"\'<.,!?]', '', display).strip()
        display = re.sub(r'\n{3,}', '\n\n', display)
    parts.append(display)

    cur = prices.get("current")
    if cur and CONFIG["PRICE_COMPARISON"]:
        card = ["\n💰 *Price Analysis*", f"├ Current: ₹{cur:,}"]
        if prices.get("original"):    card.append(f"├ MRP: ~~₹{prices['original']:,}~~")
        if prices.get("discount_percent"): card.append(f"├ Discount: *{prices['discount_percent']}% OFF* 🔥")
        if prices.get("savings"):     card.append(f"└ बचत: *₹{prices['savings']:,}* 💚")
        parts.append("\n".join(card))

    msg = "\n".join(parts)
    return msg[:4000] if len(msg) > 4000 else msg

# ──────────────────────────────────────────────────────────────
#  MAIN BOT
# ──────────────────────────────────────────────────────────────
class DealHunterBot:
    def __init__(self):
        self.client = TelegramClient('deal_hunter_session', CONFIG["API_ID"], CONFIG["API_HASH"])
        self.stats = {"processed":0,"posted":0,"filtered":0,"duplicates":0,
                      "earnkaro":0,"cuelinks":0,"start_time":datetime.now()}
        self.paused = False
        self.http = None

    async def start(self):
        # ✅ Web server PEHLE start karo
        await start_web_server()

        self.http = aiohttp.ClientSession()

        # ✅ Bot Token se login (phone nahi)
        await self.client.start(bot_token=CONFIG["BOT_TOKEN"])
        log.info("✅ Deal Hunter Bot v3.1 Started!")

        engines = []
        if CONFIG["EARNKARO_TOKEN"]: engines.append("EarnKaro ✅")
        if CONFIG["CUELINKS_PID"]:   engines.append("Cuelinks ✅")
        if not engines:               engines.append("Koi engine configure nahi")

        await self.notify_admin(
            f"🤖 *Deal Hunter Bot v3.1 — Live on Render!*\n\n"
            f"⚡ Engines: {' | '.join(engines)}\n"
            f"📡 Channels: {len(CONFIG['SOURCE_CHANNELS'])}\n"
            f"💰 Filter: ₹{CONFIG['MIN_PRICE']}–₹{CONFIG['MAX_PRICE']}\n"
            f"⏰ {datetime.now().strftime('%d/%m %H:%M')}"
        )

        self.register_handlers()
        await self.client.run_until_disconnected()

    def register_handlers(self):
        @self.client.on(events.NewMessage(chats=CONFIG["SOURCE_CHANNELS"]))
        async def on_deal(event):
            if not self.paused:
                try:
                    await self.process_deal(event.message)
                except Exception as e:
                    log.error(f"Handler error (bot nahi rukega): {e}")

        @self.client.on(events.NewMessage(from_users=CONFIG["ADMIN_ID"]))
        async def on_admin(event):
            try:
                await self.handle_admin(event)
            except Exception as e:
                log.error(f"Admin handler error: {e}")

    async def process_deal(self, message):
        try:
            raw = message.text or message.caption or ""
            if not raw or len(raw) < 20: return

            self.stats["processed"] += 1
            if is_duplicate(raw):
                self.stats["duplicates"] += 1
                return

            prices = extract_prices(raw)
            ok, _ = is_valid_deal(prices)
            if not ok:
                self.stats["filtered"] += 1
                return

            cleaned = clean_message(raw)
            converted_text, link_info = await convert_all_links(self.http, cleaned)

            for info in link_info:
                if info["changed"]:
                    if info["platform"] in ("flipkart","meesho"): self.stats["earnkaro"] += 1
                    else: self.stats["cuelinks"] += 1

            final_msg = format_message(converted_text, prices, link_info)
            buttons = build_buttons(link_info, prices)
            kwargs = {"parse_mode": "md"}
            if buttons: kwargs["buttons"] = buttons

            if message.photo:
                await self.client.send_message(CONFIG["MY_CHANNEL"], final_msg, file=message.photo, **kwargs)
            else:
                await self.client.send_message(CONFIG["MY_CHANNEL"], final_msg, **kwargs)

            self.stats["posted"] += 1
            log.info(f"✅ Posted | ₹{prices.get('current','?')} | {prices.get('discount_percent','?')}% off")
            await asyncio.sleep(2)

        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)

    async def handle_admin(self, event):
        cmd = event.text.strip().lower()
        if cmd == "/stats":
            up = datetime.now() - self.stats["start_time"]
            h = int(up.total_seconds()//3600)
            m = int((up.total_seconds()%3600)//60)
            total = max(self.stats["processed"], 1)
            await event.respond(
                f"📊 *Stats*\n\n⏱️ {h}h {m}m\n"
                f"📥 Processed: {self.stats['processed']}\n✅ Posted: {self.stats['posted']}\n"
                f"❌ Filtered: {self.stats['filtered']}\n🔄 Duplicates: {self.stats['duplicates']}\n"
                f"🔗 EarnKaro: {self.stats['earnkaro']}\n🔗 Cuelinks: {self.stats['cuelinks']}\n"
                f"📈 Rate: {round(self.stats['posted']/total*100)}%", parse_mode='md')
        elif cmd.startswith("/post "):
            await self.client.send_message(CONFIG["MY_CHANNEL"], event.text[6:], parse_mode='md')
            await event.respond("✅ Posted!")
        elif cmd == "/pause":
            self.paused = True
            await event.respond("⏸️ Paused.")
        elif cmd == "/resume":
            self.paused = False
            await event.respond("▶️ Resumed!")
        elif cmd == "/status":
            await event.respond(
                f"{'⏸️ PAUSED' if self.paused else '🟢 Running'}\n"
                f"EarnKaro: {'✅' if CONFIG['EARNKARO_TOKEN'] else '❌'}\n"
                f"Cuelinks: {'✅' if CONFIG['CUELINKS_PID'] else '❌'}", parse_mode='md')
        elif cmd == "/help":
            await event.respond(
                "🤖 *Commands*\n/stats /status /pause /resume\n/post <msg> /help", parse_mode='md')

    async def notify_admin(self, msg):
        try:
            if CONFIG["ADMIN_ID"]:
                await self.client.send_message(CONFIG["ADMIN_ID"], msg, parse_mode='md')
        except Exception as e:
            log.error(f"Notify failed: {e}")

async def main():
    bot = DealHunterBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
