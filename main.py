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

def round_to_tick(price, tick_size):
    return math.floor(float(price) / tick_size) * tick_size

def get_position_qty(symbol):
    try:
        positions = client.get_position_risk(symbol=symbol)
        for p in positions:
            if p["symbol"] == symbol:
                return float(p["positionAmt"])
        return 0.0
    except:
        return 0.0

@app.post("/webhook")
async def webhook(request: Request):
    data   = await request.json()
    action = data.get("action")
    symbol = data.get("instrument", "").replace(".P", "")
    print(f"Gelen sinyal: {data}")

    if not action or not symbol:
        return {"error": "Eksik veri"}

    try:
        # === ENTER LONG ===
        if action == "ENTER_LONG":
            leverage = get_max_leverage(symbol)
            qty_precision, price_precision, tick_size = get_symbol_info(symbol)
            client.change_leverage(symbol=symbol, leverage=leverage)

            price    = float(client.ticker_price(symbol=symbol)["price"])
            raw_qty  = NOTIONAL / price
            quantity = math.floor(raw_qty * 10**qty_precision) / 10**qty_precision
            print(f"ENTER_LONG: {symbol}, fiyat={price}, miktar={quantity}, kaldıraç={leverage}x")

            order = client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=quantity)
            filled_qty = float(order.get("origQty", quantity))
            print(f"Market emri: {order}")

            results = {"market": order, "tp_orders": [], "sl_order": None}

            # TP emirleri
            tp_percents = [("tp1", 40), ("tp2", 30), ("tp3", 20), ("tp4", 100)]
            remaining = filled_qty
            for tp_key, pct in tp_percents:
                if data.get(tp_key):
                    tp_price = round_to_tick(float(data[tp_key]), tick_size)
                    tp_price = round(tp_price, price_precision)
                    tp_qty   = math.floor(filled_qty * pct / 100 * 10**qty_precision) / 10**qty_precision
                    if tp_qty <= 0:
                        continue
                    try:
                        tp_order = client.new_order(
                            symbol=symbol, side="SELL", type="LIMIT",
                            price=str(tp_price), quantity=tp_qty,
                            reduceOnly="true", timeInForce="GTC"
                        )
                        results["tp_orders"].append(tp_order)
                        print(f"{tp_key} emri: {tp_price}, miktar: {tp_qty}")
                    except Exception as e:
                        print(f"{tp_key} hatası: {e}")

            # SL emri
            if data.get("sl"):
                sl_price = round_to_tick(float(data["sl"]), tick_size)
                sl_price = round(sl_price, price_precision)
                try:
                    sl_order = client.new_order(
                        symbol=symbol, side="SELL",
                        type="STOP_MARKET",
                        stopPrice=str(sl_price),
                        closePosition="true",
                        workingType="CONTRACT_PRICE",
                        timeInForce="GTE_GTC"
                    )
                    results["sl_order"] = sl_order
                    print(f"SL emri: {sl_price}")
                except Exception as e:
                    print(f"SL hatası: {e}")

            return {"status": "ok", **results}

        # === ENTER SHORT ===
        elif action == "ENTER_SHORT":
            leverage = get_max_leverage(symbol)
            qty_precision, price_precision, tick_size = get_symbol_info(symbol)
            client.change_leverage(symbol=symbol, leverage=leverage)

            price    = float(client.ticker_price(symbol=symbol)["price"])
            raw_qty  = NOTIONAL / price
            quantity = math.floor(raw_qty * 10**qty_precision) / 10**qty_precision
            print(f"ENTER_SHORT: {symbol}, fiyat={price}, miktar={quantity}, kaldıraç={leverage}x")

            order = client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=quantity)
            filled_qty = float(order.get("origQty", quantity))
            print(f"Market emri: {order}")

            results = {"market": order, "tp_orders": [], "sl_order": None}

            tp_percents = [("tp1", 40), ("tp2", 30), ("tp3", 20), ("tp4", 100)]
            for tp_key, pct in tp_percents:
                if data.get(tp_key):
                    tp_price = round_to_tick(float(data[tp_key]), tick_size)
                    tp_price = round(tp_price, price_precision)
                    tp_qty   = math.floor(filled_qty * pct / 100 * 10**qty_precision) / 10**qty_precision
                    if tp_qty <= 0:
                        continue
                    try:
                        tp_order = client.new_order(
                            symbol=symbol, side="BUY", type="LIMIT",
                            price=str(tp_price), quantity=tp_qty,
                            reduceOnly="true", timeInForce="GTC"
                        )
                        results["tp_orders"].append(tp_order)
                        print(f"{tp_key} emri: {tp_price}, miktar: {tp_qty}")
                    except Exception as e:
                        print(f"{tp_key} hatası: {e}")

            if data.get("sl"):
                sl_price = round_to_tick(float(data["sl"]), tick_size)
                sl_price = round(sl_price, price_precision)
                try:
                    sl_order = client.new_order(
                        symbol=symbol, side="BUY",
                        type="STOP_MARKET",
                        stopPrice=str(sl_price),
                        closePosition="true",
                        workingType="CONTRACT_PRICE",
                        timeInForce="GTE_GTC"
                    )
                    results["sl_order"] = sl_order
                    print(f"SL emri: {sl_price}")
                except Exception as e:
                    print(f"SL hatası: {e}")

            return {"status": "ok", **results}

        # === EXIT LONG (kısmi kapanış) ===
        elif action == "EXIT_LONG":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty  = get_position_qty(symbol)
            pct      = float(data.get("qty_percent", 100)) / 100
            close_qty = math.floor(abs(pos_qty) * pct * 10**qty_precision) / 10**qty_precision
            if close_qty <= 0:
                return {"status": "skip", "reason": "pozisyon yok"}
            order = client.new_order(symbol=symbol, side="SELL", type="MARKET",
                                     quantity=close_qty, reduceOnly="true")
            print(f"EXIT_LONG: {close_qty} kapatıldı, sebep={data.get('reason')}")
            return {"status": "ok", "order": order}

        # === EXIT SHORT (kısmi kapanış) ===
        elif action == "EXIT_SHORT":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty   = get_position_qty(symbol)
            pct       = float(data.get("qty_percent", 100)) / 100
            close_qty = math.floor(abs(pos_qty) * pct * 10**qty_precision) / 10**qty_precision
            if close_qty <= 0:
                return {"status": "skip", "reason": "pozisyon yok"}
            order = client.new_order(symbol=symbol, side="BUY", type="MARKET",
                                     quantity=close_qty, reduceOnly="true")
            print(f"EXIT_SHORT: {close_qty} kapatıldı, sebep={data.get('reason')}")
            return {"status": "ok", "order": order}

        # === CLOSE ALL ===
        elif action == "CLOSE_ALL":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty = get_position_qty(symbol)
            if pos_qty == 0:
                return {"status": "skip", "reason": "pozisyon yok"}
            side      = "SELL" if pos_qty > 0 else "BUY"
            close_qty = math.floor(abs(pos_qty) * 10**qty_precision) / 10**qty_precision
            # Önce açık emirleri iptal et
            try:
                client.cancel_open_orders(symbol=symbol)
            except:
                pass
            order = client.new_order(symbol=symbol, side=side, type="MARKET",
                                     quantity=close_qty, reduceOnly="true")
            print(f"CLOSE_ALL: {symbol}, sebep={data.get('reason')}")
            return {"status": "ok", "order": order}

        else:
            return {"error": f"Bilinmeyen action: {action}"}

    except Exception as e:
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.get("/")
def root():
    return {"status": "Strateji botu çalışıyor"}
