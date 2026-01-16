import telebot
import logging

BOT_TOKEN = "8393149048:AAFlQc9jqwyUn18lOEe9zERFQXe3wbnad2g"
GROUP_ID = -1005109977089  # your group ID

bot = telebot.TeleBot(BOT_TOKEN)

def send_telegram(msg: str, chat_id: int = GROUP_ID):
    """Send Telegram message to the group using TeleBot."""
    try:
        bot.send_message(chat_id, msg, parse_mode="HTML")
        return True
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
        return False
send_telegram("Testing Telegram Notification", chat_id="-1005109977089")