# main.py — Bot de alertas definitivo (Yahoo + Binance, alertas reales + anticipadas)
import time
import math
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta

# ====== CONFIG ======
BOT_TOKEN = "7581025511:AAEdxP8cPlynjkbfeXTDzKz_9JPjSb5MRN4"
CHAT_ID = "8264626126"
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# PARES: clave = nombre interno, valor = fuente+yf/binance symbol
# Para Yahoo usamos símbolos válidos; para cripto guardamos binance symbol como fallback
PAIRS = {
    "XAU/USD": {"yahoo": "GC=F",     "binance": None},
    "XAU/EUR": {"yahoo": None,       "binance": None},  # se calculará como GC=F / EURUSD=X
    "GBP/JPY": {"yahoo": "GBPJPY=X", "binance": None},
    "EUR/USD": {"yahoo": "EURUSD=X", "binance": None},
    "UKOIL":   {"yahoo": "BZ=F",     "binance": None},
    "NAS100":  {"yahoo": "^NDX",     "binance": None},
    "BTC/USDT":{"yahoo": None,       "binance": "BTCUSDT"},
    "ETH/USDT":{"yahoo": None,       "binance": "ETHUSDT"},
    "GBP/USD": {"yahoo": "GBPUSD=X", "binance": None}
}

# Parámetros de alerta
MIN_MOVE_PCT = 1.0     # alerta por movimiento >= 1% (en ~4 horas)
ANTICIPATE_PCT = 0.3   # alerta anticipada cuando el precio esté dentro de 0.3% del high/low 24h
CHECK_INTERVAL_SECONDS = 300  # cada 5 minutos

# ====== UTIL TELEGRAM (mensaje mínimo) ======
def send_telegram(pair, entry, sl, tp, tag="ALERTA"):
    text = f"{pair} {tag}\nEntrada: {entry}\nStop Loss: {sl}\nTake Profit: {tp}"
    try:
        requests.post(TG_URL, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"[TG ERROR] {e}")

# ====== UTIL YAHOO ======
def yahoo_prices(symbol, period="1d", interval="1h"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, threads=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        closes = df["Close"]
        return closes
    except Exception as e:
        print(f"[Yahoo error] {symbol}: {e}")
        return None

# ====== UTIL BINANCE (KLINES) ======
def binance_klines(symbol_binance, limit=24):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1h&limit={limit}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        closes = [float(k[4]) for k in data]
        return closes
    except Exception as e:
        print(f"[Binance error] {symbol_binance}: {e}")
        return None

# ====== NIVELES (entry, SL, TP) ======
def calc_levels(price, direction_up):
    entry = round(price, 6)
    if direction_up:
        sl = round(price * 0.993, 6)   # SL ~0.7% below
        tp = round(price * 1.016, 6)   # TP ~1.6% above
    else:
        sl = round(price * 1.007, 6)   # SL ~0.7% above
        tp = round(price * 0.984, 6)   # TP ~1.6% below
    return entry, sl, tp

# ====== LÓGICA DE ALERTA POR MOVIMIENTO (%) ======
def check_move_yahoo(symbol_yf):
    closes = yahoo_prices(symbol_yf, period="1d", interval="1h")
    if closes is None or len(closes) < 5:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-5])
    pct = ((last - prev) / prev) * 100.0
    return last, pct, closes

def check_move_binance(symbol_binance):
    closes = binance_klines(symbol_binance, limit=6)
    if closes is None or len(closes) < 5:
        return None
    last = float(closes[-1])
    prev = float(closes[-5])
    pct = ((last - prev) / prev) * 100.0
    return last, pct, closes

# ====== ALERTA ANTICIPADA: cerca de high/low 24h (usamos últimas 24 barras 1h) ======
def check_anticipate_from_closes(pair_name, closes_series):
    # closes_series puede ser lista o pandas Series (de 24 valores)
    arr = list(closes_series[-24:]) if len(closes_series) >= 1 else []
    if len(arr) < 5:
        return None
    last = float(arr[-1])
    high24 = max(arr)
    low24 = min(arr)
    # si está dentro de ANTICIPATE_PCT del high o low -> anticipada
    pct_to_high = (high24 - last) / high24 * 100.0 if high24 != 0 else 999
    pct_to_low = (last - low24) / low24 * 100.0 if low24 != 0 else 999
    # cerca de resistencia (alto)
    if pct_to_high <= ANTICIPATE_PCT:
        # anticipada bajista (posible reversa) -> consideramos venta (direction_up=False)
        entry, sl, tp = calc_levels(last, direction_up=False)
        return entry, sl, tp, "ANTICIPADA"
    # cerca de soporte (bajo)
    if pct_to_low <= ANTICIPATE_PCT:
        # anticipada alcista -> compra
        entry, sl, tp = calc_levels(last, direction_up=True)
        return entry, sl, tp, "ANTICIPADA"
    return None

# ====== Revisión por par (un solo flujo: intentar Yahoo, si no Binance para cripto) ======
def check_pair(pair_name, info):
    # Especial: XAU/EUR calculado como GC=F / EURUSD=X (si posible)
    if pair_name == "XAU/EUR":
        closes_xau = yahoo_prices("GC=F", period="1d", interval="1h")
        closes_eur = yahoo_prices("EURUSD=X", period="1d", interval="1h")
        if closes_xau is None or closes_eur is None or len(closes_xau) < 5 or len(closes_eur) < 5:
            return None
        # calcular ratio por índice (precio oro en USD / EURUSD) -> precio en EUR
        ratio = (closes_xau / closes_eur)
        last = float(ratio.iloc[-1])
        prev = float(ratio.iloc[-5])
        pct = ((last - prev) / prev) * 100.0
        # movimiento fuerte?
        if abs(pct) >= MIN_MOVE_PCT:
            entry, sl, tp = calc_levels(last, direction_up=(pct > 0))
            send_telegram(pair_name, entry, sl, tp, tag="ALERTA")
            return "sent"
        # anticipada?
        ant = check_anticipate_from_closes(pair_name, list(ratio[-24:]) if len(ratio)>=24 else list(ratio))
        if ant:
            entry, sl, tp, tag = ant
            send_telegram(pair_name, entry, sl, tp, tag=tag)
            return "sent"
        return None

    # 1) intentar Yahoo si disponible
    yf_sym = info.get("yahoo")
    bin_sym = info.get("binance")
    # Yahoo path
    if yf_sym:
        res = check_move_yahoo(yf_sym)
        if res:
            last, pct, closes = res
            if abs(pct) >= MIN_MOVE_PCT:
                entry, sl, tp = calc_levels(last, direction_up=(pct>0))
                send_telegram(pair_name, entry, sl, tp, tag="ALERTA")
                return "sent"
            # anticipada con closes (últimas 24h)
            if len(closes) >= 24:
                ant = check_anticipate_from_closes(pair_name, list(closes[-24:]))
                if ant:
                    entry, sl, tp, tag = ant
                    send_telegram(pair_name, entry, sl, tp, tag=tag)
                    return "sent"
    # 2) fallback Binance (solo si está definido)
    if bin_sym:
        res = check_move_binance(bin_sym)
        if res:
            last, pct, closes = res
            if abs(pct) >= MIN_MOVE_PCT:
                entry, sl, tp = calc_levels(last, direction_up=(pct>0))
                send_telegram(pair_name, entry, sl, tp, tag="ALERTA")
                return "sent"
            if len(closes) >= 24:
                ant = check_anticipate_from_closes(pair_name, closes[-24:])
                if ant:
                    entry, sl, tp, tag = ant
                    send_telegram(pair_name, entry, sl, tp, tag=tag)
                    return "sent"
    return None

# ====== LOOP PRINCIPAL ======
def main_loop():
    print("Iniciando chequeos — solo alertas útiles se enviarán.")
    while True:
        start = datetime.now(timezone.utc)
        for pair_name, info in PAIRS.items():
            try:
                check_pair(pair_name, info)
            except Exception as e:
                print(f"[ERROR pair {pair_name}] {e}")
        # esperar intervalo
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        sleep_for = max(5, CHECK_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)

if __name__ == "__main__":
    main_loop()
