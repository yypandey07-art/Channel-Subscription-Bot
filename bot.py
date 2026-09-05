import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- RENDER KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running and healthy!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIGURATION (Environment Variables) ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']
posts_col = db['premium_posts']

# --- ADMIN LOGIC ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    # User entry via Deep Link
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                # Display Dynamic Plans
                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Days"
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(message.chat.id, 
                    f"Welcome!\n\nYou are joining: *{ch_data['name']}*.\n\nPlease select a subscription plan below:", 
                    reply_markup=markup, parse_mode="Markdown")
                return
        except: pass

    # Admin Panel Greeting
    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ Admin Panel Active!\n\n/add - Add/Edit Channel & Prices\n/channels - Manage Existing Channels\n/addpost - Create a post with Ads + Premium buttons")
    else:
        bot.send_message(message.chat.id, "Welcome! To join a channel, please use the link provided by the Admin.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    # Fetch all channels managed by this admin
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"Channel: {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    
    markup.add(InlineKeyboardButton("➕ Add New Channel", callback_data="add_new"))
    
    if count == 0:
        bot.send_message(ADMIN_ID, "No channels found. Click below to add one.", reply_markup=markup)
    else:
        bot.send_message(ADMIN_ID, "Your Managed Channels:", reply_markup=markup)

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Please ensure the bot is an Admin in your channel, then FORWARD any message from that channel here.")
    bot.register_next_step_handler(msg, get_plans)

# Callback for Add New button
@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def cb_add_new(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "Please FORWARD any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, 
            f"Channel Detected: *{ch_name}*\n\n"
f"Enter plans in format (Days:Price):\n"
"Days:Price, Days:Price\n\n"
"Example:\n15:69, 30:99 (15 Days and 30 Days)"
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Message was not forwarded. Use /add to try again.")

def finalize_channel(message, ch_id, ch_name):
    try:
        raw_plans = message.text.strip().split(',')
        plans_dict = {}

        for p in raw_plans:
            parts = p.strip().split(':', 1)

            if len(parts) != 2:
                raise ValueError("Use Days:Price format")

            days = int(parts[0].strip())
            price = int(parts[1].strip())

            if days <= 0 or price < 0:
                raise ValueError("Days/Price must be valid numbers")

            # Convert days to minutes
            minutes = days * 24 * 60

            plans_dict[str(minutes)] = str(price)

        channels_col.update_one(
            {"channel_id": ch_id},
            {
                "$set": {
                    "name": ch_name,
                    "plans": plans_dict,
                    "admin_id": ADMIN_ID
                }
            },
            upsert=True
        )

        bot_username = bot.get_me().username

        bot.send_message(
            ADMIN_ID,
            f"✅ Setup Successful!\n\n"
            f"Plans: {message.text.strip()}\n\n"
            f"Invite Link:\n"
            f"https://t.me/{bot_username}?start={ch_id}"
        )

    except Exception as e:
        bot.send_message(
            ADMIN_ID,
            "❌ Invalid format.\n\n"
            "Use: Days:Price, Days:Price\n"
            "Example: 15:69, 30:99"
        )

# --- USER: PAYMENT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"Plan: {mins} Minutes\nPrice: ₹{price}\nUPI ID: `{UPI_ID}`\n\nPlease complete the payment and click 'I Have Paid'.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    _, ch_id, mins = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_message(ADMIN_ID, f"🔔 *Payment Verification Required!*\n\nUser: {user.first_name}\nChannel: {ch_data['name']}\nPlan: {mins} Mins\nPrice: ₹{price}", 
                     reply_markup=markup, parse_mode="Markdown")
    
    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, "✅ Your payment request has been sent. Please wait for Admin approval.", reply_markup=u_markup)

# --- APPROVAL & EXPIRY ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)

    try:
        expiry_datetime = datetime.now() + timedelta(minutes=mins)

        users_col.update_one(
            {"user_id": u_id},
            {"$set": {
                "premium": True,
                "expiry": expiry_datetime.timestamp()
            }},
            upsert=True
        )

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👑 Premium Users – Click Here",
                                        callback_data="premium_menu"))

        bot.send_message(
            u_id,
            f"🥳 *Premium Activated!*
\n"
            f"Subscription: {mins} Minutes\n"
            f"Expires: {expiry_datetime.strftime('%d-%m-%Y %I:%M %p')}",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.edit_message_text(
            f"✅ Approved user {u_id} for {mins} mins.",
            call.message.chat.id,
            call.message.message_id
        )

    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}")


# --- PREMIUM POST SYSTEM ---

@bot.message_handler(commands=['addpost'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_post_start(message):
    msg = bot.send_message(
        ADMIN_ID,
        "➕ Send the *Ads/Shortener link* for this post:",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, get_ads_link)


def get_ads_link(message):
    ads_link = (message.text or "").strip()
    if not ads_link.startswith(("http://", "https://")):
        bot.send_message(ADMIN_ID, "❌ Please send a valid http/https link.")
        return

    msg = bot.send_message(
        ADMIN_ID,
        "🔐 Now send the *direct Telegram premium content link*.",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, save_premium_post, ads_link)


def save_premium_post(message, ads_link):
    premium_link = (message.text or "").strip()
    if not premium_link.startswith(("http://", "https://")):
        bot.send_message(ADMIN_ID, "❌ Please send a valid Telegram post/file link.")
        return

    post = posts_col.insert_one({
        "ads_link": ads_link,
        "premium_link": premium_link,
        "created_at": datetime.now()
    })

    post_id = str(post.inserted_id)

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔗 Get Content (Ads)", url=ads_link)
    )
    markup.add(
        InlineKeyboardButton(
            "👑 Premium Users – Click Here",
            callback_data=f"premium_{post_id}"
        )
    )

    bot.send_message(
        ADMIN_ID,
        f"✅ Premium post created!\n\n"
        f"Post ID: `{post_id}`\n\n"
        "Copy/use the buttons below with your post.",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("premium_"))
def premium_post_click(call):
    from bson import ObjectId

    try:
        post_id = call.data.split("_", 1)[1]
        post = posts_col.find_one({"_id": ObjectId(post_id)})

        if not post:
            bot.answer_callback_query(call.id, "❌ Post not found.", show_alert=True)
            return

        user = users_col.find_one({"user_id": call.from_user.id})
        now = datetime.now().timestamp()

        if not user or not user.get("premium") or user.get("expiry", 0) <= now:
            bot.answer_callback_query(
                call.id,
                "❌ Premium required. Please buy Premium first.",
                show_alert=True
            )

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("💎 Buy Premium", callback_data="buy_premium"))
            bot.send_message(
                call.message.chat.id,
                "🔒 This content is for Premium users only.",
                reply_markup=markup
            )
            return

        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📥 Open Premium Content", url=post["premium_link"]))
        bot.send_message(
            call.message.chat.id,
            "✅ Premium verified!\n\nYour direct content link:",
            reply_markup=markup
        )

    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Something went wrong.", show_alert=True)
        bot.send_message(ADMIN_ID, f"❌ Premium post error: {e}")


@bot.callback_query_handler(func=lambda call: call.data == "premium_menu")
def premium_menu(call):
    user = users_col.find_one({"user_id": call.from_user.id})
    if user and user.get("premium") and user.get("expiry", 0) > datetime.now().timestamp():
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "👑 Premium is active.\n\n"
            "Open a premium post and tap "
            "“👑 Premium Users – Click Here”."
        )
    else:
        bot.answer_callback_query(call.id, "Premium expired.", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "buy_premium")
def buy_premium_from_button(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "💎 Please use the subscription link provided by the Admin to buy Premium."
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start={ch_id}"
    
    bot.edit_message_text(f"Settings for: *{ch_data['name']}*\n\nYour Link: `{link}`\n\nTo edit prices, use /add and forward a message from this channel again.", 
                          call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# Automate Kicking
def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({
        "premium": True,
        "expiry": {"$lte": now}
    })

    for user in expired_users:
        try:
            users_col.update_one(
                {"_id": user["_id"]},
                {"$set": {"premium": False}}
            )
            bot.send_message(
                user["user_id"],
                "⚠️ Your Premium subscription has expired.\n\n"
                "Please purchase Premium again to access premium content."
            )
        except Exception:
            pass


# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    bot.remove_webhook()
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
