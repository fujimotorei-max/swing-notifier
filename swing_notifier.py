import yfinance as yf
import pandas as pd
import json
import os
import requests
import pytz

STATE_FILE = "trade_state.json"
RESET_FILE = "manual_reset.json"
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
JST = pytz.timezone("Asia/Tokyo")

# ===== LINE =====
def send_line(message: str):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
               "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": message}]}
    r = requests.post(url, headers=headers, json=data)
    print("LINE送信:", r.status_code, r.text[:200])

# ===== state I/O =====
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ===== manual reset =====
def load_reset():
    if os.path.exists(RESET_FILE):
        with open(RESET_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def clear_reset():
    with open(RESET_FILE, "w") as f:
        json.dump({}, f)

# ===== util: 30分足→日足合成 =====
def build_daily_from_30m(df_30m: pd.DataFrame) -> pd.DataFrame:
    if df_30m.empty:
        return df_30m
    if df_30m.index.tz is None:
        df_30m = df_30m.tz_localize("UTC")
    df_30m = df_30m.tz_convert(JST)
    df_30m = df_30m.between_time("09:00", "15:00")

    daily = pd.DataFrame({
        "Open":  df_30m["Open"].resample("1D").first(),
        "High":  df_30m["High"].resample("1D").max(),
        "Low":   df_30m["Low"].resample("1D").min(),
        "Close": df_30m["Close"].resample("1D").last(),
    }).dropna()

    # SMA計算（短期5・中期25・長期75）
    daily["SMA5"]  = daily["Close"].rolling(5).mean()
    daily["SMA25"] = daily["Close"].rolling(25).mean()
    daily["SMA75"] = daily["Close"].rolling(75).mean()
    return daily.dropna()

# ===== ウォッチ銘柄 =====
watchlist = {
    "4503.T": "アステラス製薬", "4568.T": "第一三共",   "5401.T": "日本製鉄",
    "6178.T": "日本郵政",       "1605.T": "INPEX",     "1928.T": "積水ハウス",
    "3659.T": "ネクソン",       "6326.T": "クボタ",     "6503.T": "三菱電機",
    "6752.T": "パナソニック",   "7182.T": "ゆうちょ銀行","7203.T": "トヨタ自動車",
    "7270.T": "SUBARU",        "7733.T": "オリンパス", "7751.T": "キヤノン",
    "8053.T": "住友商事",       "8267.T": "イオン",     "8306.T": "三菱UFJ",
    "8411.T": "みずほFG",       "8591.T": "オリックス", "8750.T": "第一生命HD",
    "8766.T": "東京海上HD",     "8801.T": "三井不動産", "8802.T": "三菱地所",
    "9434.T": "ソフトバンク",   "9503.T": "関西電力",   "9613.T": "NTTデータ"
}

# ===== メイン =====
def run(mode="intraday"):
    state = load_state()

    # 手動リセット
    reset_cmd = load_reset()
    if reset_cmd:
        done = []
        for code in list(reset_cmd.keys()):
            state[code] = {"status": "NONE"}
            done.append(code)
        if done:
            send_line("🔄 手動リセット: " + ", ".join(done))
        clear_reset()

    for code, name in watchlist.items():
        print(f"=== {code} {name} ===")
        tstate = state.get(code, {"status": "NONE"})

        # ---------- 日足：エントリー判定 ----------
        if mode == "daily":
            raw = yf.download(code, period="200d", interval="30m", progress=False)
            if raw.empty:
                print("データなし"); state[code] = tstate; continue

            daily = build_daily_from_30m(raw)
            if len(daily) < 2:
                print("日足不足"); state[code] = tstate; continue

            price = float(daily["Close"].iloc[-1])
            prev  = daily.iloc[-2]
            curr  = daily.iloc[-1]

            # 条件A: ゴールデンクロス（SMA5がSMA25を上抜け）
            gcross = (prev["SMA5"] <= prev["SMA25"]) and (curr["SMA5"] > curr["SMA25"])

            # 条件B: トレンド整列（短期>中期>長期）
            prev_align = (prev["SMA5"] > prev["SMA25"] > prev["SMA75"])
            curr_align = (curr["SMA5"] > curr["SMA25"] > curr["SMA75"])
            align_new  = (not prev_align) and curr_align

            if tstate["status"] == "NONE" and (gcross or align_new):
                stop_loss  = price * 0.97
                take_profit = price * 1.06
                reason = "ゴールデンクロス" if gcross else "短期>中期>長期の整列"
                send_line(
                    f"⚡️【{name}({code})】日足エントリーシグナル（{reason}）\n"
                    f"終値: {price:.0f}円\n"
                    f"📈 利確ライン: {take_profit:.0f}円\n"
                    f"📉 損切りライン: {stop_loss:.0f}円\n"
                    f"👉 OCO注文をセットしてください"
                )
                tstate = {"status": "HOLD", "entry_price": float(price)}

            state[code] = tstate
            continue

        # ---------- 場中：利確/損切のみ ----------
        df = yf.download(code, period="5d", interval="30m", progress=False)
        if df.empty:
            print("データなし"); state[code] = tstate; continue

        if tstate["status"] == "HOLD":
            entry = float(tstate["entry_price"])
            sl = entry * 0.97
            tp = entry * 1.06
            high = float(df["High"].iloc[-1])
            low  = float(df["Low"].iloc[-1])
            close = float(df["Close"].iloc[-1])

            if low <= sl:
                send_line(
                    f"❌【{name}({code})】損切りライン到達\n"
                    f"現在値: {close:.0f}円 / 取得: {entry:.0f}円\n"
                    f"OCOで決済済みのはずです"
                )
                tstate = {"status": "NONE"}

            elif high >= tp:
                send_line(
                    f"✅【{name}({code})】利確ライン到達\n"
                    f"現在値: {close:.0f}円 / 取得: {entry:.0f}円\n"
                    f"OCOで決済済みのはずです"
                )
                tstate = {"status": "NONE"}

        state[code] = tstate

    save_state(state)

if __name__ == "__main__":
    run(mode=os.environ.get("RUN_MODE", "intraday"))
