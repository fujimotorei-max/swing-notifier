import yfinance as yf
import pandas as pd
import json
import os
import requests

STATE_FILE = "trade_state.json"
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
def run():
    state = load_state()

    for code, name in watchlist.items():
        print(f"=== {code} {name} ===")

        df = yf.download(code, period="5d", interval="30m")
        if df.empty:
            print("データなし")
            continue

        # ✅ floatに変換して常にスカラー値にする
        price = float(df["Close"].iloc[-1])
        print("現在値:", price)

        tstate = state.get(code, {"status": "NONE"})

        # 取引中（HOLD）の場合は通知なし → OCOはエントリー時点でセット済み
        if tstate["status"] == "HOLD":
            pass  

        # 未保有 → エントリーシグナル判定（移動平均クロス）
        else:
            df["SMA5"] = df["Close"].rolling(5).mean()
            df["SMA20"] = df["Close"].rolling(20).mean()
            df = df.dropna()

            if len(df) >= 2:
                prev = df.iloc[-2]
                curr = df.iloc[-1]

                prev_sma5 = float(prev["SMA5"])
                prev_sma20 = float(prev["SMA20"])
                curr_sma5 = float(curr["SMA5"])
                curr_sma20 = float(curr["SMA20"])

                # クロス検出
                if prev_sma5 <= prev_sma20 and curr_sma5 > curr_sma20:
                    stop_loss = price * 0.97
                    take_profit = price * 1.06
                    send_line(
                        f"⚡️【{name}({code})】エントリーシグナル\n"
                        f"現在値: {price:.0f}円\n"
                        f"📉 損切りライン: {stop_loss:.0f}円\n"
                        f"📈 利確ライン: {take_profit:.0f}円\n"
                        f"👉 OCO注文をセットしてください"
                    )
                    tstate = {"status": "HOLD", "entry_price": float(price)}

        state[code] = tstate

    save_state(state)


if __name__ == "__main__":
    run()
