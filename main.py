import os
import re
import time
import random
import requests
import threading
from bs4 import BeautifulSoup
from http.server import HTTPServer, BaseHTTPRequestHandler

# =====================================================================
# НАСТРОЙКИ VK И ПАРСИНГА
# =====================================================================
VK_TOKEN = "vk1.a.8pB-oX3qMRdazAWQoQFH2LXwAebuddJNCZ7Eqseg0sHHD_mcWoQyWwxQf5GLuaXfYFL9AilymOTMGgCsYqIvZ1aDNSgn6qLGIkmn9m0AIa_s6yqXky3573c0o0LKNAuklOqVXTWSj2ScJUoujSz1sTcM6cIufm9CXwr7J0Ek7wr7igNIaljER-qOmIP_Q9v87AddkHu7qi5285Lhgrmu0A"
VK_USER_ID = 698764992

FUNPAY_CHIPS_URL = "https://funpay.com/chips/140/"

# --- ФИЛЬТРЫ ЦЕНЫ И НАЛИЧИЯ (АНТИ-ФЕЙК) ---
MAX_PRICE_PER_GOLD = 0.45    # Верхняя планка: до 45 копеек
MIN_PRICE_PER_GOLD = 0.30    # Нижняя планка: от 30 копеек
MIN_STOCK = 1000             # Наличие строго от 1000G
MAX_STOCK = 500000           # Максимальное наличие: отсекаем 9999999

CHECK_INTERVAL = 15          # Проверка каждые 15 секунд
GOLDEN_KEY = "ujzw57uy1nn32vje2mxma4mfnmhamr58"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
}

COOKIES = {
    "cy": "rub",
    "golden_key": GOLDEN_KEY
}

seen_lots = set()

# =====================================================================
# ПАРСИНГ ЧИСЕЛ И ЦЕН
# =====================================================================
def extract_float(text: str) -> float:
    if not text:
        return 0.0
    clean = text.replace("\xa0", "").replace(" ", "").replace("₽", "").replace("$", "").replace("€", "").strip()
    match = re.search(r"(\d*[.,]?\d+)", clean)
    if not match:
        return 0.0
    val_str = match.group(1).replace(",", ".")
    if val_str.startswith("."):
        val_str = "0" + val_str
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def get_lot_price_and_stock(item):
    stock = 0
    amount_el = item.select_one(".tc-amount") or item.select_one(".tc-order-amount")
    if amount_el:
        stock = int(extract_float(amount_el.get_text(strip=True)))

    price_el = item.select_one(".tc-price")
    if not price_el:
        return 0.0, stock

    for tag in price_el.find_all(["span", "small"]):
        if any(c in tag.get_text() for c in ["₽", "$", "€", "руб"]):
            tag.decompose()

    raw_price_str = price_el.get_text(strip=True).replace("\xa0", "").replace(" ", "")
    parsed_price = extract_float(raw_price_str)
    unit_price = parsed_price

    if stock > 1 and parsed_price > 1.5:
        calc_per_unit = round(parsed_price / stock, 4)
        if 0.05 <= calc_per_unit <= 1.5:
            unit_price = calc_per_unit

    return round(unit_price, 4), stock

# =====================================================================
# ОТПРАВКА VK
# =====================================================================
def send_vk(message: str):
    url = "https://api.vk.com/method/messages.send"
    for _ in range(3):
        try:
            params = {
                "access_token": VK_TOKEN,
                "user_id": VK_USER_ID,
                "random_id": random.randint(1, 2147483647),
                "message": message,
                "v": "5.199"
            }
            resp = requests.post(url, data=params, timeout=10)
            data = resp.json()
            if "response" in data:
                print("[+] Уведомление доставлено в VK!")
                time.sleep(1.2)
                return
            elif "error" in data:
                if data["error"].get("error_code") == 9:
                    time.sleep(3)
                    continue
                else:
                    print(f"[-] Ошибка VK: {data}")
                    return
        except Exception as e:
            print(f"[-] Сеть VK: {e}")
            time.sleep(2)

# =====================================================================
# СКАНИРОВАНИЕ
# =====================================================================
def scan_gold():
    try:
        resp = requests.get(FUNPAY_CHIPS_URL, headers=HEADERS, cookies=COOKIES, timeout=15)
        if resp.status_code != 200:
            print(f"[-] FunPay HTTP {resp.status_code}")
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".tc-item")
        if not items:
            items = soup.select("a.tc-item, div.tc-item")

        print(f"[*] Считано строк таблицы: {len(items)}")

        printed_debug = 0

        for item in items:
            href = item.get("href")
            if not href:
                a_tag = item.find("a", href=True)
                href = a_tag["href"] if a_tag else ""

            if not href:
                continue

            full_link = f"https://funpay.com{href}" if href.startswith("/") else href
            unit_price, stock = get_lot_price_and_stock(item)
            
            user_el = item.select_one(".media-user-name")
            desc_el = item.select_one(".tc-desc-text") or item.select_one(".tc-desc")
            seller = user_el.get_text(strip=True) if user_el else "Продавец"
            desc = desc_el.get_text(strip=True) if desc_el else "Без описания"

            if printed_debug < 3 and unit_price > 0:
                print(f"    [Тест парсера] {seller} -> Курс: {unit_price} ₽/G | Наличие: {stock}G")
                printed_debug += 1

            if stock < MIN_STOCK or stock > MAX_STOCK or unit_price <= 0:
                continue

            lot_key = f"{href}_{unit_price}_{stock}"
            if lot_key in seen_lots:
                continue

            if MIN_PRICE_PER_GOLD <= unit_price <= MAX_PRICE_PER_GOLD:
                total_sum = round(stock * unit_price, 2)
                print(f"\n[🔥 НАЙДЕН РЕАЛЬНЫЙ ЛОТ] {unit_price} ₽/G | Наличие: {stock}G | {seller}")

                msg = (
                    f"🚨 ВЫГОДНАЯ ГОЛДА STANDOFF 2!\n\n"
                    f"💰 Курс: {unit_price} ₽ за 1G\n"
                    f"📦 В наличии: {stock:,}G (~{total_sum} ₽)\n"
                    f"👤 Продавец: {seller}\n"
                    f"📝 Инфо: {desc}\n\n"
                    f"🔗 Ссылка: {full_link}"
                )
                send_vk(msg)

            seen_lots.add(lot_key)

    except requests.RequestException as e:
        print(f"[-] Ошибка сети: {e}")

# =====================================================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# =====================================================================
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), WebHandler)
    server.serve_forever()

# =====================================================================
# ЗАПУСК
# =====================================================================
def main():
    t = threading.Thread(target=run_web_server, daemon=True)
    t.start()

    print("=" * 65)
    print("  СНАЙПЕР ГОЛДЫ STANDOFF 2 ЗАПУЩЕН")
    print(f"  Раздел: {FUNPAY_CHIPS_URL}")
    print(f"  Критерии: {MIN_PRICE_PER_GOLD} ₽ - {MAX_PRICE_PER_GOLD} ₽ | Наличие: {MIN_STOCK}G - {MAX_STOCK}G")
    print("=" * 65 + "\n")

    send_vk(
        f"⚡ Снайпер голды запущен!\n"
        f"Раздел: chips/140\n"
        f"Курс: от {MIN_PRICE_PER_GOLD} до {MAX_PRICE_PER_GOLD} ₽\n"
        f"Наличие: от {MIN_STOCK:,}G"
    )

    while True:
        scan_gold()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
