# main.py
# Bot definitivo de alertas automáticas (Yahoo + Binance) -> Telegram
# Incluye token y chat_id proporcionados por el usuario.

import time
import math
import requests
import yfinance as yf
from datetime import datetime, timezone
import traceback

# ========== CONFIG ==========
BOT_TOKEN = "7581025511:AAEdxP8cPlynjkbfeXTDzKz_9JPjSb5MRN4"
CHAT_ID = "8264626126"
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

CHECK_INTERVAL_SECONDS = 300        # intervalo de chequeo (5 minutos)
MIN_MOVE_PCT = 1.0                  # umbral movimiento fuerte (%) para ALERTA
ANTICIPATE_PCT = 0.3                # umbral anticipada respecto high/low 24h (%)

# PARES monitorizados
PAIRS = {
    "XAU/USD": {"yahoo": "GC=F", "binance": None},
    "XAU/EUR": {"yahoo": None, "binance": None},  # calculado como GC=F / EURUSD=X
    "GBP/JPY": {"yahoo": "GBPJPY=X", "binance": None},
    "EUR/USD": {"yahoo": "EURUSD=X", "binance": None},
    "UKOIL":   {"yahoo": "BZ=F", "binance": None},   # Brent
    "NAS100":  {"yahoo": "^NDX", "binance": None},
    "BTC/USDT":{"yahoo": None, "binance": "BTCUSDT"},
    "ETH/USDT":{"yahoo": None, "binance": "ETHUSDT"},
    "GBP/USD": {"yahoo": "GBPUSD=X", "binance": None}
}

# ========== UTIL TELEGRAM ==========
def send_telegram_text(text):
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(TG_URL, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TG ERROR] {e}")
        return False

def send_alert(pair, entry, sl, tp, tag, price=None, pct=None):
    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [f"{pair} — {tag}"]
    if price is not None:
        parts.append(f"Precio: {price}")
    if pct is not None:
        parts.append(f"Cambio: {pct:+.2f}%")
    parts.append(f"Entrada: {entry}")
    parts.append(f"Stop Loss: {sl}")
    parts.append(f"Take Profit: {tp}")
    parts.append(f"Hora: {time_str}")
    text = "\n".join(parts)
    sent = send_telegram_text(text)
    print(f"[ALERTA SENT] {pair} {tag} sent={sent}")

# ========== UTIL YAHOO ==========
def yahoo_closes(symbol, period="2d", interval="1h"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, threads=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df["Close"]
    except Exception as e:
        print(f"[YF ERROR] {symbol}: {e}")
        return None

# ========== UTIL BINANCE ==========
def binance_closes(symbol_binance, limit=48):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1h&limit={limit}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            print(f"[BIN ERROR] {symbol_binance} status {r.status_code}")
            return None
        data = r.json()
        closes = [float(k[4]) for k in data]
        return closes
    except Exception as e:
        print(f"[BIN ERROR] {symbol_binance}: {e}")
        return None

# ========== NIVELES ==========
def calc_levels(price, direction_up):
    price = float(price)
    entry = round(price, 6)
    if direction_up:
        sl = round(price * 0.993, 6)   # ~0.7% abajo
        tp = round(price * 1.016, 6)   # ~1.6% arriba
    else:
        sl = round(price * 1.007, 6)   # ~0.7% arriba
        tp = round(price * 0.984, 6)   # ~1.6% abajo
    return entry, sl, tp

# ========== CÁLCULO MOVIMIENTO / ANTICIPADA ==========
def pct_change_from_series(closes, lookback=5):
    # espera que closes sea lista o pandas Series de al menos lookback elementos
    try:
        if closes is None:
            return None
        if hasattr(closes, "iloc"):
            n = len(closes)
            if n < lookback:
                return None
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-lookback])
        else:
            n = len(closes)
            if n < lookback:
                return None
            last = float(closes[-1])
            prev = float(closes[-lookback])
        pct = ((last - prev) / prev) * 100.0
        return last, pct
    except Exception as e:
        print(f"[PCT ERROR] {e}")
        return None

def check_anticipate(closes_24h):
    # recibe lista/series de (hasta) 24 valores hora a hora
    try:
        arr = list(closes_24h)[-24:]
        if len(arr) < 5:
            return None
        last = float(arr[-1])
        high24 = max(arr)
        low24 = min(arr)
        if high24 == 0 or low24 == 0:
            return None
        pct_to_high = (high24 - last) / high24 * 100.0
        pct_to_low = (last - low24) / low24 * 100.0
        if pct_to_high <= ANTICIPATE_PCT:
            entry, sl, tp = calc_levels(last, direction_up=False)
            return entry, sl, tp, "ANTICIPADA"
        if pct_to_low <= ANTICIPATE_PCT:
            entry, sl, tp = calc_levels(last, direction_up=True)
            return entry, sl, tp, "ANTICIPADA"
        return None
    except Exception as e:
        print(f"[ANTICIPATE ERROR] {e}")
        return None

# ========== CHECK POR PAR ==========
def check_pair(pair_name, info):
    try:
        # caso especial XAU/EUR -> calcular como GC=F / EURUSD=X
        if pair_name == "XAU/EUR":
            closes_xau = yahoo_closes("GC=F", period="2d", interval="1h")
            closes_eur = yahoo_closes("EURUSD=X", period="2d", interval="1h")
            if closes_xau is None or closes_eur is None or len(closes_xau) < 5 or len(closes_eur) < 5:
                return
            ratio = (closes_xau / closes_eur)
            res = pct_change_from_series(ratio, lookback=5)
            if res:
                last, pct = res
                if abs(pct) >= MIN_MOVE_PCT:
                    entry, sl, tp = calc_levels(last, direction_up=(pct>0))
                    send_alert(pair_name, entry, sl, tp, "ALERTA", price=last, pct=pct)
                    return
                if len(ratio) >= 24:
                    ant = check_anticipate(list(ratio[-24:]))
                    if ant:
                        entry, sl, tp, tag = ant
                        send_alert(pair_name, entry, sl, tp, tag, price=float(ratio.iloc[-1]), pct=None)
                        return
            return

        # intento Yahoo si existe
        yf_sym = info.get("yahoo")
        bin_sym = info.get("binance")

        if yf_sym:
            closes = yahoo_closes(yf_sym, period="2d", interval="1h")
            if closes is not None and len(closes) >= 5:
                r = pct_change_from_series(closes, lookback=5)
                if r:
                    last, pct = r
                    if abs(pct) >= MIN_MOVE_PCT:
                        entry, sl, tp = calc_levels(last, direction_up=(pct>0))
                        send_alert(pair_name, entry, sl, tp, "ALERTA", price=last, pct=pct)
                        return
                if len(closes) >= 24:
                    ant = check_anticipate(list(closes[-24:]))
                    if ant:
                        entry, sl, tp, tag = ant
                        send_alert(pair_name, entry, sl, tp, tag, price=float(closes.iloc[-1]), pct=None)
                        return

        # fallback Binance para cripto si está definido
        if bin_sym:
            closes = binance_closes(bin_sym, limit=48)
            if closes is not None and len(closes) >= 5:
                last = float(closes[-1])
                prev = float(closes[-5])
                pct = ((last - prev) / prev) * 100.0
                if abs(pct) >= MIN_MOVE_PCT:
                    entry, sl, tp = calc_levels(last, direction_up=(pct>0))
                    send_alert(pair_name, entry, sl, tp, "ALERTA", price=last, pct=pct)
                    return
                if len(closes) >= 24:
                    ant = check_anticipate(closes[-24:])
                    if ant:
                        entry, sl, tp, tag = ant
                        send_alert(pair_name, entry, sl, tp, tag, price=last, pct=None)
                        return
    except Exception:
        print(f"[CHECK_PAIR ERROR] {pair_name}\n{traceback.format_exc()}")

# ========== LOOP PRINCIPAL ==========
def main_loop():
    print("Bot de alertas corriendo — enviará SOLO alertas válidas (ALERTA / ANTICIPADA).")
    while True:
        start = datetime.now(timezone.utc)
        for pair_name, info in PAIRS.items():
            check_pair(pair_name, info)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        sleep_time = max(5, CHECK_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_time)

if __name__ == "__main__":
    main_loop()
