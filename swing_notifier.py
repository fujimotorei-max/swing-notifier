import os
import json
import time
import pytz
import requests
import pandas as pd
import yfinance as yf

# ====== 設定 ======
STATE_FILE = "trade_state.json"
RESET_FILE = "manual_reset.json"
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
JST = pytz.timezone("Asia/Tokyo")

# パフォーマンス調整
WATCH_LIMIT = int(os.environ.get("WATCH_LIMIT", "150"))      # 1回の実行で見る最大銘柄数
TIME_SLEEP_MS = int(os.environ.get("TIME_SLEEP_MS", "120"))  # yfinance連打抑制

# 利確/損切（エントリー価格に対する倍率）
TP_MULT = float(os.environ.get("TP_MULT", "1.06"))  # +6%
SL_MULT = float(os.environ.get("SL_MULT", "0.97"))  # -3%

# ===== LINE =====
def send_line(message: str):
    if not CHANNEL_ACCESS_TOKEN:
        print("[WARN] CHANNEL_ACCESS_TOKEN 未設定のため LINE送信をスキップ")
        return
    try:
        url = "https://api.line.me/v2/bot/message/broadcast"
        headers = {
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        data = {"messages": [{"type": "text", "text": message}]}
        r = requests.post(url, headers=headers, json=data, timeout=10)
        print("LINE送信:", r.status_code, r.text[:200])
    except Exception as e:
        print("[ERROR] LINE送信に失敗:", e)

# ===== state I/O =====
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ===== manual reset =====
def load_reset():
    if os.path.exists(RESET_FILE):
        with open(RESET_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def clear_reset():
    with open(RESET_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False)

def apply_manual_reset(state: dict):
    reset_cmd = load_reset()
    if not reset_cmd:
        return state
    done = []
    for code in list(reset_cmd.keys()):
        state[code] = {"status": "NONE"}
        done.append(code)
    if done:
        send_line("🔄 手動リセット: " + ", ".join(done))
    clear_reset()
    return state

# ===== ウォッチ銘柄 =====
from watchlist_module import watchlist  # 既存の巨大watchlistを使用

# ===== メイン =====
def run(mode="daily"):
    """
    daily: 日足でのみ監視。GC(5>25上抜け) かつ 5>25>75整列 完成日に1回通知。
           利確/損切ラインは同時に提示。到達通知はしない。
    intraday: 何もしない（no-op）
    """
    assert mode in ("daily", "intraday")

    state = load_state()
    state = apply_manual_reset(state)

    if mode == "intraday":
        print("intraday mode: no-op")
        save_state(state)
        return

    items = list(watchlist.items())[:WATCH_LIMIT]

    for idx, (code, name) in enumerate(items, 1):
        print(f"\n=== [{idx}/{len(items)}] {code} {name} ===")
        tstate = state.get(code, {"status": "NONE"})

        try:
            raw = yf.download(code, period="400d", interval="1d", progress=False)
            if raw is None or raw.empty:
                print("データなし")
                state[code] = tstate
                continue

            daily = raw.copy()
            for w in (5, 25, 75):
                daily[f"SMA{w}"] = daily["Close"].rolling(w).mean()
            daily = daily.dropna()

            if len(daily) < 2:
                print("日足不足")
                state[code] = tstate
                continue

            prev = daily.iloc[-2]
            curr = daily.iloc[-1]
            price = float(curr["Close"])

            # 条件：GC かつ 整列
            gcross = (prev["SMA5"] <= prev["SMA25"]) and (curr["SMA5"] > curr["SMA25"])
            aligned = (curr["SMA5"] > curr["SMA25"] > curr["SMA75"])
            entry_signal = gcross and aligned

            # 既にHOLDなら重複通知しない
            if tstate.get("status") == "NONE" and entry_signal:
                tp = price * TP_MULT
                sl = price * SL_MULT
                send_line(
                    f"⚡️【{name}({code})】日足エントリー（GC＋整列）\n"
                    f"終値: {price:.0f}円\n"
                    f"📈 利確ライン: {tp:.0f}円（×{TP_MULT}）\n"
                    f"📉 損切りライン: {sl:.0f}円（×{SL_MULT}）\n"
                    f"👉 OCO注文をセットしてください"
                )
                tstate = {"status": "HOLD", "entry_price": float(price)}

            state[code] = tstate

        except Exception as e:
            print(f"[ERROR] {code} 処理中に例外:", e)
            state[code] = tstate

        save_state(state)
        if TIME_SLEEP_MS > 0:
            time.sleep(TIME_SLEEP_MS / 1000.0)

    save_state(state)

if __name__ == "__main__":
    run(mode=os.environ.get("RUN_MODE", "daily"))
