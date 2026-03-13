"""
═══════════════════════════════════════════════
  SESSION GENERATOR - एक बार चलाओ, फिर भूल जाओ
  
  यह script आपका Telegram session बनाएगी।
  इसे अपने PC/laptop पर run करो।
═══════════════════════════════════════════════
"""

import asyncio
import base64
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

print("=" * 50)
print("   Deal Hunter Bot - Session Generator")
print("=" * 50)
print()
print("⚠️  यह script एक बार ही चलाओ!")
print("📋 my.telegram.org पर जाकर API credentials लो")
print()

API_ID = int(input("🔑 API ID डालो: ").strip())
API_HASH = input("🔑 API Hash डालो: ").strip()
PHONE = input("📱 Phone number (+91XXXXXXXXXX): ").strip()

async def generate_session():
    print("\n⏳ Session generate हो रही है...")
    
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        await client.start(phone=PHONE)
        
        session_string = client.session.save()
        
        # Base64 encode for GitHub Secrets
        session_b64 = base64.b64encode(
            session_string.encode()
        ).decode()
        
        print("\n" + "=" * 50)
        print("✅ SESSION GENERATED SUCCESSFULLY!")
        print("=" * 50)
        print()
        print("📋 यह SESSION_STRING GitHub Secret में add करो:")
        print()
        print(f"SESSION_STRING = {session_string}")
        print()
        print("=" * 50)
        print("🔐 GitHub Secrets Setup:")
        print("  1. GitHub repo → Settings → Secrets → Actions")
        print("  2. 'New repository secret' click करो")
        print("  3. नीचे दिए सभी secrets add करो:")
        print()
        print(f"  TG_API_ID     = {API_ID}")
        print(f"  TG_API_HASH   = {API_HASH}")
        print(f"  TG_PHONE      = {PHONE}")
        print(f"  SESSION_STRING = [ऊपर वाला string]")
        print(f"  MY_CHANNEL    = @YourChannelName")
        print(f"  ADMIN_ID      = [आपका Telegram User ID]")
        print(f"  AMAZON_TAG    = yourtag-21")
        print("=" * 50)
        
        # Save to file as backup
        with open("session_backup.txt", "w") as f:
            f.write(f"API_ID={API_ID}\n")
            f.write(f"API_HASH={API_HASH}\n")
            f.write(f"SESSION_STRING={session_string}\n")
        
        print("\n✅ session_backup.txt में भी save हो गया")
        print("⚠️  इस file को किसी को मत दिखाना!")
        print("\n🎉 अब GitHub पर push करो और bot start हो जाएगा!")

asyncio.run(generate_session())
