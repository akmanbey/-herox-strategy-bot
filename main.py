from fastapi import FastAPI, Request
from binance.um_futures import UMFutures
import os
import traceback
import math

app = FastAPI()

API_KEY    = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")
client     = UMFutures(key=API_KEY, secret=API_SECRET)

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
        max_lev  = brackets[0]["brackets"][0]["initialLeverage"]
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
        # ============================================================
        # ENTER LONG — Sadece market buy + SL
        # TP emirleri YOK — TradingView EXIT_LONG alertleri halleder
        # ============================================================
        if action == "ENTER_LONG":
            leverage = get_max_leverage(symbol)
            qty_precision, price_precision, tick_size = get_symbol_info(symbol)
            client.change_leverage(symbol=symbol, leverage=leverage)

            # Önceki açık emirleri temizle (eski SL vs.)
            try:
                client.cancel_open_orders(symbol=symbol)
            except Exception as e:
                print(f"Emir iptal hatası: {e}")

            price    = float(client.ticker_price(symbol=symbol)["price"])
            raw_qty  = NOTIONAL / price
            quantity = math.floor(raw_qty * 10**qty_precision) / 10**qty_precision
            print(f"ENTER_LONG: {symbol} | fiyat={price} | miktar={quantity} | kaldıraç={leverage}x")

            order      = client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=quantity)
            filled_qty = float(order.get("executedQty", quantity))
            print(f"Market emri doldu: {filled_qty} adet")

            results = {"market": order, "sl_order": None}

            # Sadece SL — fiyat ve miktar TradingView'dan geliyor
            if data.get("sl"):
                sl_price = round(round_to_tick(float(data["sl"]), tick_size), price_precision)
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

        # ============================================================
        # ENTER SHORT — Sadece market sell + SL
        # ============================================================
        elif action == "ENTER_SHORT":
            leverage = get_max_leverage(symbol)
            qty_precision, price_precision, tick_size = get_symbol_info(symbol)
            client.change_leverage(symbol=symbol, leverage=leverage)

            try:
                client.cancel_open_orders(symbol=symbol)
            except Exception as e:
                print(f"Emir iptal hatası: {e}")

            price    = float(client.ticker_price(symbol=symbol)["price"])
            raw_qty  = NOTIONAL / price
            quantity = math.floor(raw_qty * 10**qty_precision) / 10**qty_precision
            print(f"ENTER_SHORT: {symbol} | fiyat={price} | miktar={quantity} | kaldıraç={leverage}x")

            order      = client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=quantity)
            filled_qty = float(order.get("executedQty", quantity))
            print(f"Market emri doldu: {filled_qty} adet")

            results = {"market": order, "sl_order": None}

            if data.get("sl"):
                sl_price = round(round_to_tick(float(data["sl"]), tick_size), price_precision)
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

        # ============================================================
        # EXIT LONG — Miktar ve % tamamen TradingView'dan gelir
        # TradingView gönderir: qty_percent=40 → pozisyonun %40'ı kapanır
        # ============================================================
        elif action == "EXIT_LONG":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty   = get_position_qty(symbol)

            if pos_qty <= 0:
                return {"status": "skip", "reason": "long pozisyon yok"}

            # qty_percent TradingView'dan gelir (TP1=40, TP2=50, TP3=67, TP4=100)
            pct       = float(data.get("qty_percent", 100)) / 100
            close_qty = math.floor(abs(pos_qty) * pct * 10**qty_precision) / 10**qty_precision

            if close_qty <= 0:
                return {"status": "skip", "reason": "hesaplanan miktar sıfır"}

            order = client.new_order(
                symbol=symbol, side="SELL", type="MARKET",
                quantity=close_qty, reduceOnly="true"
            )
            reason = data.get("reason", "?")
            print(f"EXIT_LONG: {close_qty} adet kapatıldı | sebep={reason} | kalan≈{abs(pos_qty)-close_qty:.4f}")
            return {"status": "ok", "order": order}

        # ============================================================
        # EXIT SHORT — Miktar ve % tamamen TradingView'dan gelir
        # ============================================================
        elif action == "EXIT_SHORT":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty   = get_position_qty(symbol)

            if pos_qty >= 0:
                return {"status": "skip", "reason": "short pozisyon yok"}

            pct       = float(data.get("qty_percent", 100)) / 100
            close_qty = math.floor(abs(pos_qty) * pct * 10**qty_precision) / 10**qty_precision

            if close_qty <= 0:
                return {"status": "skip", "reason": "hesaplanan miktar sıfır"}

            order = client.new_order(
                symbol=symbol, side="BUY", type="MARKET",
                quantity=close_qty, reduceOnly="true"
            )
            reason = data.get("reason", "?")
            print(f"EXIT_SHORT: {close_qty} adet kapatıldı | sebep={reason} | kalan≈{abs(pos_qty)-close_qty:.4f}")
            return {"status": "ok", "order": order}

        # ============================================================
        # CLOSE ALL — Tüm pozisyonu kapat + açık emirleri iptal et
        # ============================================================
        elif action == "CLOSE_ALL":
            qty_precision, _, _ = get_symbol_info(symbol)
            pos_qty = get_position_qty(symbol)

            if pos_qty == 0:
                return {"status": "skip", "reason": "pozisyon yok"}

            try:
                client.cancel_open_orders(symbol=symbol)
            except:
                pass

            side      = "SELL" if pos_qty > 0 else "BUY"
            close_qty = math.floor(abs(pos_qty) * 10**qty_precision) / 10**qty_precision

            order = client.new_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=close_qty, reduceOnly="true"
            )
            reason = data.get("reason", "?")
            print(f"CLOSE_ALL: {symbol} | sebep={reason}")
            return {"status": "ok", "order": order}

        else:
            return {"error": f"Bilinmeyen action: {action}"}

    except Exception as e:
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.get("/")
def root():
    return {"status": "Strateji botu çalışıyor"}
