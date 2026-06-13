from fastapi import FastAPI, Request
from binance.um_futures import UMFutures
import os
import traceback
import math

app = FastAPI()

API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")
client = UMFutures(key=API_KEY, secret=API_SECRET)

MAX_LEVERAGE = 20
NOTIONAL     = 10

def get_symbol_info(symbol):
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                qty_precision   = s["quantityPrecision"]
                price_precision = s["pricePrecision"]
                tick_size = 0.01
                for f in s["filters"]:
                    if f["filterType"] == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                        break
                return qty_precision, price_precision, tick_size
        return 3, 2, 0.01
    except:
        return 3, 2, 0.01

def get_max_leverage(symbol):
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        max_lev = brackets[0]["brackets"][0]["initialLeverage"]
        return min(max_lev, MAX_LEVERAGE)
    except:
        return 10

def get_position(symbol):
    try:
        positions = client.get_position_risk(symbol=symbol)
        for p in positions:
            if p["symbol"] == symbol:
                return p
        return None
    except:
        return None

@app.post("/webhook")
async def webhook(request: Request):
    data        = await request.json()
    action      = data.get("action")
    symbol      = data.get("instrument", "").replace(".P", "")
    qty_percent = float(data.get("qty_percent", 100)) / 100

    print(f"Gelen sinyal: action={action}, symbol={symbol}, qty_percent={qty_percent}")

    if not action or not symbol:
        return {"error": "Eksik veri"}

    # ── KISMI ÇIKIŞ ──────────────────────────────────────────────
    if action in ("EXIT_LONG", "EXIT_SHORT"):
        try:
            pos = get_position(symbol)
            if pos is None or abs(float(pos["positionAmt"])) == 0:
                return {"status": "no_position"}

            pos_qty = abs(float(pos["positionAmt"]))
            qty_precision, _, _ = get_symbol_info(symbol)
            close_side = "SELL" if action == "EXIT_LONG" else "BUY"
            close_qty  = math.floor(pos_qty * qty_percent * 10**qty_precision) / 10**qty_precision

            print(f"Kısmi çıkış: {close_side} {close_qty} / toplam {pos_qty} (%{qty_percent*100})")

            order = client.new_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )
            print(f"Kısmi çıkış emri: {order}")
            return {"status": "ok", "order": order}
        except Exception as e:
            print(traceback.format_exc())
            return {"status": "error", "message": str(e)}

    # ── TAM KAPAT ─────────────────────────────────────────────────
    if action == "CLOSE_ALL":
        try:
            try:
                client.cancel_open_orders(symbol=symbol)
            except:
                pass

            pos = get_position(symbol)
            if pos is None or abs(float(pos["positionAmt"])) == 0:
                return {"status": "no_position"}

            pos_qty    = abs(float(pos["positionAmt"]))
            close_side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"

            order = client.new_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=pos_qty,
                reduceOnly=True
            )
            print(f"CLOSE_ALL: {order}")
            return {"status": "ok", "order": order}
        except Exception as e:
            print(traceback.format_exc())
            return {"status": "error", "message": str(e)}

    # ── YENİ POZİSYON ─────────────────────────────────────────────
    if action not in ("ENTER_LONG", "ENTER_SHORT"):
        return {"status": "ignored", "action": action}

    side = "BUY" if action == "ENTER_LONG" else "SELL"

    try:
        leverage = get_max_leverage(symbol)
        qty_precision, _, _ = get_symbol_info(symbol)
        client.change_leverage(symbol=symbol, leverage=leverage)
        print(f"Kaldıraç: {leverage}x")

        price    = float(client.ticker_price(symbol=symbol)["price"])
        raw_qty  = NOTIONAL / price
        quantity = math.floor(raw_qty * 10**qty_precision) / 10**qty_precision
        print(f"Fiyat: {price}, Miktar: {quantity}")

        try:
            client.cancel_open_orders(symbol=symbol)
        except:
            pass

        order = client.new_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        print(f"Market emri: {order}")
        return {"status": "ok", "leverage": leverage, "order": order}

    except Exception as e:
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.get("/")
def root():
    return {"status": "Bot çalışıyor"}
