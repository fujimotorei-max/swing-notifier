import yfinance as yf
import pandas as pd
import json
import os
import requests
import datetime
import pytz

STATE_FILE = "trade_state.json"
RESET_FILE = "manual_reset.json"
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

# LINE通知
def send_line(message: str):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"messages": [{"type": "text", "text": message}]}
    r = requests.post(url, headers=headers, json=data)
    print("LINE送信:", r.status_code, r.text[:200])

# 状態のロード・保存
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# リセットファイル処理
def load_reset():
    if os.path.exists(RESET_FILE):
        with open(RESET_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def clear_reset():
    with open(RESET_FILE, "w") as f:
        json.dump({}, f)

# 銘柄リスト
watchlist = {
    "4503.T": "アステラス製薬",
    "4568.T": "第一三共",
    "5401.T": "日本製鉄",
    "6178.T": "日本郵政",
    "1605.T": "INPEX",
    "1928.T": "積水ハウス",
    "3659.T": "ネクソン",
    "6326.T": "クボタ",
    "6503.T": "三菱電機",
    "6752.T": "パナソニック",
    "7182.T": "ゆうちょ銀行",
    "7203.T": "トヨタ自動車",
    "7270.T": "SUBARU",
    "7733.T": "オリンパス",
    "7751.T": "キヤノン",
    "8053.T": "住友商事",
    "8267.T": "イオン",
    "8306.T": "三菱UFJ",
    "8411.T": "みずほFG",
    "8591.T": "オリックス",
    "8750.T": "第一生命HD",
    "8766.T": "東京海上HD",
    "8801.T": "三井不動産",
    "8802.T": "三菱地所",
    "9434.T": "ソフトバンク",
    "9503.T": "関西電力",
    "9613.T": "NTTデータ"
}

# メイン処理
def run(mode="intraday"):
    state = load_state()

    # 手動リセット処理
    reset_cmd = load_reset()
    if reset_cmd:
        reset_list = []
        for code in list(reset_cmd.keys()):
            state[code] = {"status": "NONE"}
            reset_list.append(code)
            print(f"[RESET] {code} を強制解除しました")
        if reset_list:
            send_line("🔄 手動リセット: " + ", ".join(reset_list))
        clear_reset()

    for code, name in watchlist.items():
        print(f"=== {code} {name} ===")

        # データ取得（日足 or 30分足）
        interval = "1d" if mode == "daily" else "30m"
        df = yf.download(code, period="30d", interval=interval, progress=False)
        if df.empty:
            print("データなし")
            continue

        price = df["Close"].iloc[-1]
        tstate = state.get(code, {"status": "NONE"})

        # =====================
        # 利確・損切チェック（場中のみ）
        # =====================
        if mode == "intraday" and tstate["status"] == "HOLD":
            entry_price = tstate["entry_price"]
            stop_loss = entry_price * 0.97
            take_profit = entry_price * 1.06

            high = float(df["High"].iloc[-1])
            low = float(df["Low"].iloc[-1])
            close = float(df["Close"].iloc[-1])

            if low <= stop_loss:
                send_line(
                    f"❌【{name}({code})】損切りライン到達\n"
                    f"現在値: {close:.0f}円\n"
                    f"エントリー価格: {entry_price:.0f}円\n"
                    f"OCO注文で決済済みのはずです"
                )
                tstate = {"status": "NONE"}

            elif high >= take_profit:
                send_line(
                    f"✅【{name}({code})】利確ライン到達\n"
                    f"現在値: {close:.0f}円\n"
                    f"エントリー価格: {entry_price:.0f}円\n"
                    f"OCO注文で決済済みのはずです"
                )
                tstate = {"status": "NONE"}

        # =====================
        # エントリー判定（日足のみ）
        # =====================
        elif mode == "daily" and tstate["status"] == "NONE":
            df["SMA5"] = df["Close"].rolling(5).mean()
            df["SMA20"] = df["Close"].rolling(20).mean()
            df = df.dropna()

            if len(df) >= 2:
                prev = df.iloc[-2]
                curr = df.iloc[-1]
                if prev["SMA5"] <= prev["SMA20"] and curr["SMA5"] > curr["SMA20"]:
                    stop_loss = price * 0.97
                    take_profit = price * 1.06
                    send_line(
                        f"⚡️【{name}({code})】日足エントリーシグナル\n"
                        f"現在値: {price:.0f}円\n"
                        f"📈 利確ライン: {take_profit:.0f}円\n"
                        f"📉 損切りライン: {stop_loss:.0f}円\n"
                        f"👉 OCO注文をセットしてください"
                    )
                    tstate = {"status": "HOLD", "entry_price": float(price)}

        state[code] = tstate

    save_state(state)

if __name__ == "__main__":
    # デフォルトは intraday（場中監視）
    run(mode=os.environ.get("RUN_MODE", "intraday"))
