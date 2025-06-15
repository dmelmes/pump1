# pump_scan_final.py (KESİNLİK ARTIŞLI VERSİYON)
# Binance + Gate.io + Etherscan + CoinGecko verisiyle erken PUMP adayı tespit sistemi

import requests
import pandas as pd
import numpy as np
import time
import os
import csv
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN1")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID1")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")

session = requests.Session()

QUOTE = "USDT"
MIN_VOLUME = 200_000
MIN_TRADES = 1000
PUMP_SCORE_THRESHOLD = 7

# Token CSV dosyasını oku (birleşik Binance + Gateio listesi)
token_address_map = {}
coingecko_id_map = {}
always_watch_list = set()
try:
    with open("sabitcoin.txt", "r", encoding="utf-8") as f:
        for line in f:
            sym = line.strip().upper()
            if sym:
                always_watch_list.add(sym)
except:
    pass
borsa_map = {}
TOKEN_CSV_PATH = "Token_Listesi.csv"
if os.path.exists(TOKEN_CSV_PATH):
    with open(TOKEN_CSV_PATH, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            symbol = row['symbol'].upper()
            token_address_map[symbol] = row['token_address']
            coingecko_id_map[symbol] = row['coingecko_id']
            borsa_map[symbol] = row.get('borsa', 'binance').lower()
            if row.get('always_watch', '').strip().lower() == 'true':
                always_watch_list.add(symbol)

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        session.post(url, data=payload, timeout=15)
    except Exception as e:
        print(f"Telegram mesajı gönderilemedi: {e}")

def get_erc20_transfers_zamanli(token_address, start_timestamp):
    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": token_address,
        "starttimestamp": start_timestamp,
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY
    }
    try:
        r = session.get(url, params=params, timeout=10)
        data = r.json()
        if data["status"] != "1":
            return []
        transfers = []
        for tx in data["result"]:
            amount = int(tx["value"]) / (10 ** int(tx["tokenDecimal"]))
            transfers.append({"from": tx["from"], "to": tx["to"], "value": amount, "symbol": tx["tokenSymbol"]})
        return transfers
    except:
        return []

def get_binance_ohlc(symbol, interval="1m", limit=50):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = session.get(url, params=params, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"])
        df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df
    except:
        return None

def get_gateio_ohlc(symbol, interval="1m", limit=50):
    url = f"https://api.gate.io/api/v4/spot/candlesticks"
    params = {"currency_pair": symbol.lower(), "interval": interval, "limit": limit}
    try:
        r = session.get(url, params=params, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=["timestamp", "volume", "close", "high", "low", "open"])
        df = df.iloc[::-1].reset_index(drop=True)
        df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df
    except:
        return None

def pump_score(df):
    try:
        vol_now = df['volume'].iloc[-5:].sum()
        vol_prev = df['volume'].iloc[-10:-5].sum()
        vol_chg = (vol_now - vol_prev) / (vol_prev + 1e-8) * 100
        vol_chg = min(vol_chg, 100_000)
        price_now = df['close'].iloc[-1]
        price_prev = df['close'].iloc[-6]
        price_chg = (price_now - price_prev) / price_prev * 100
        delta = df['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        roll_up = up.rolling(14).mean()
        roll_down = down.rolling(14).mean()
        rs = roll_up / (roll_down + 1e-8)
        rsi = 100.0 - (100.0 / (1.0 + rs)).iloc[-1]
        obv = [0]
        for i in range(1, len(df)):
            if df['close'].iloc[i] > df['close'].iloc[i-1]:
                obv.append(obv[-1] + df['volume'].iloc[i])
            elif df['close'].iloc[i] < df['close'].iloc[i-1]:
                obv.append(obv[-1] - df['volume'].iloc[i])
            else:
                obv.append(obv[-1])
        obv_up = obv[-1] > obv[-6]
        highest = df['high'].rolling(5).max()
        lowest = df['low'].rolling(5).min()
        willr = -100 * (highest - df['close']) / (highest - lowest + 1e-8)
        willr_val = willr.iloc[-1]
        breakout = df['close'].iloc[-1] > df['high'].iloc[-15:-1].max() * 1.01
        ema25 = df['close'].rolling(25).mean()
        ema_kirma = price_now > ema25.iloc[-1]
        son2hacim_up = df['volume'].iloc[-1] > df['volume'].iloc[-2] > df['volume'].iloc[-3]

        score = 0
        if vol_chg > 300: score += 3
        if 2 < price_chg < 6: score += 2
        if rsi > 55 and obv_up: score += 1
        if willr_val > -50: score += 1
        if breakout: score += 1
        if ema_kirma: score += 1
        if son2hacim_up: score += 1

        return score, {
            "vol_chg": vol_chg,
            "price_chg": price_chg,
            "rsi": rsi,
            "obv_up": obv_up,
            "willr": willr_val,
            "breakout": breakout,
            "price": price_now
        }
    except:
        return 0, {}

def get_coin_data(coin_id):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        r = session.get(url, timeout=10)
        data = r.json()
        price = data["market_data"]["current_price"]["usd"]
        supply = data["market_data"]["circulating_supply"]
        return price, supply
    except:
        return None, None

def main():
    print(f"{datetime.now().strftime('%H:%M:%S')} ⏳ Tarama başladı...")
    toplam_coin = 0
    taranan_coin = 0
    for symbol in coingecko_id_map:
        toplam_coin += 1
        borsa = borsa_map.get(symbol, 'binance')
        pair = symbol + QUOTE

        if borsa == 'binance':
            df = get_binance_ohlc(pair)
        elif borsa == 'gateio':
            df = get_gateio_ohlc(pair)
        else:
            continue

        if df is None or len(df) < 20:
            continue

        score, det = pump_score(df)
        taranan_coin += 1
        whale_info = ""
        token_addr = token_address_map.get(symbol)
        cg_id = coingecko_id_map.get(symbol)

        if token_addr and (score >= 6 or symbol in always_watch_list):
            zaman_dilimleri = [
                ("🕐 5dk", 300),
                ("🕒 15dk", 900),
                ("🕕 30dk", 1800),
                ("🕗 1saat", 3600),
                ("🕓 4saat", 14400),
                ("📅 1gün", 86400)
            ]
            whale_flows = []
            whale_alerts = []
            price, supply = get_coin_data(cg_id)
            if price and supply:
                for label, seconds in zaman_dilimleri:
                    start_time = int(time.time()) - seconds
                    txs = get_erc20_transfers_zamanli(token_addr, start_time)
                    incoming = sum(tx['value'] for tx in txs if tx['to'].lower() != '0x0000000000000000000000000000000000000000')
                    outgoing = sum(tx['value'] for tx in txs if tx['from'].lower() != '0x0000000000000000000000000000000000000000')
                    net = incoming - outgoing
                    usd_net = net * price
                    if incoming > 0:
                        perc_in = (incoming / supply) * 100
                        if perc_in >= 0.05:
                            whale_alerts.append(f"🚨 Büyük balina girişi ({label}): %{perc_in:.4f}")
                        elif perc_in >= 0.01:
                            whale_alerts.append(f"⚠️ Orta balina girişi ({label}): %{perc_in:.4f}")
                    if outgoing > 0:
                        perc_out = (outgoing / supply) * 100
                        if perc_out >= 0.05:
                            whale_alerts.append(f"🚨 Büyük balina çıkışı ({label}): %{perc_out:.4f}")
                        elif perc_out >= 0.01:
                            whale_alerts.append(f"⚠️ Orta balina çıkışı ({label}): %{perc_out:.4f}")
                    if abs(usd_net) > 0:
                        whale_flows.append(f"{label}: {'+' if usd_net >= 0 else ''}${usd_net:,.0f}")

                if whale_flows:
                    whale_info += "🐋 Balina Net Akışı:\n" + "\n".join(whale_flows) + "\n"
                if whale_alerts:
                    whale_info += "\n" + "\n".join(whale_alerts) + "\n"

                txs = get_erc20_transfers_zamanli(token_addr, int(time.time()) - 3600)
                if txs:
                    top_tx = max(txs, key=lambda x: x['value'])
                    usd = top_tx['value'] * price
                    perc = (top_tx['value'] / supply) * 100
                    if usd > 10_000:
                        score += 1 if usd < 50_000 else 2
                        whale_info += (
                            f"🐳 En büyük balina işlemi: <code>{top_tx['value']:,.0f} {top_tx['symbol']}</code> ≈ ${usd:,.0f}\n"
                            f"🔄 Arzın %{perc:.6f}'i\n"
                        )

        if score >= 9:
            sinyal_gucu = "ÇOK GÜÇLÜ"
            hedef_yazi = "%20+ hedef (yüksek hacimli breakout)"
        elif score >= 7:
            sinyal_gucu = "GÜÇLÜ"
            hedef_yazi = "%10–20 potansiyel"
        elif score >= 6:
            sinyal_gucu = "ORTA"
            hedef_yazi = "%4–10 potansiyel"
        else:
            sinyal_gucu = "ZAYIF"
            hedef_yazi = "%2–5 potansiyel"

        # Kara liste kontrolü
        blacklist = {s for s in coingecko_id_map if borsa_map.get(s, '') == 'blacklist'}
        if symbol in blacklist or df['volume'].iloc[-1] < 1000:
            continue

        if score >= PUMP_SCORE_THRESHOLD or symbol in always_watch_list:
            msg = (
                f"<b>{symbol}{QUOTE}</b> 🚀 <b>PUMP ADAYI!</b> (Skor: {score})\n"
                f"💹 Borsa: {borsa.upper()}\n"
                f"💰 Fiyat: <code>{det['price']:.6f}</code>\n"
                f"📈 Hacim Artışı: <code>{det['vol_chg']:.2f}%</code>\n"
                f"📉 Fiyat Artışı: <code>{det['price_chg']:.2f}%</code>\n"
                f"🔹 RSI: <code>{det['rsi']:.1f}</code> | OBV: {'YUKARI' if det['obv_up'] else 'ZAYIF'}\n"
                f"🔸 Williams %R: <code>{det['willr']:.2f}</code> | Breakout: {'VAR' if det['breakout'] else 'YOK'}\n"
                f"🎯 Sinyal Gücü: {sinyal_gucu}\n🎯 Beklenen hedef: {hedef_yazi}\n"
                f"{whale_info}"
            )
            send_telegram_message(msg)
            time.sleep(1.2)

    try:
        send_telegram_message(f"🧮 Toplam taranan coin sayısı: {taranan_coin}/{toplam_coin}")
    except:
        pass

if __name__ == '__main__':
    main()
