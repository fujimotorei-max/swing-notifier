import yfinance as yf
import pandas as pd
import requests
import datetime
import os

# LINE設定
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

def send_line(message):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messages": [
            {"type": "text", "text": message}
        ]
    }
    requests.post(url, headers=headers, json=data)

# ウォッチ銘柄
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

# トレード状態管理用
positions = {}  # {ticker: {"entry": price, "status": "long"}}

def check_signal(ticker, name):
    df = yf.download(ticker, period="6mo", interval="1d")
    df["SMA5"] = df["Close"].rolling(5).mean()
    df["SMA25"] = df["Close"].rolling(25).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # すでにポジションある場合
    if ticker in positions:
        entry_price = positions[ticker]["entry"]
        current_price = latest["Close"]

        # 利確 +6%
        if current_price >= entry_price * 1.06:
            msg = f"{name} ({ticker}) 利確しました（+6%）\nエントリー: {entry_price:.2f}, 決済: {current_price:.2f}"
            send_line(msg)
            del positions[ticker]
            return

        # 損切り -3%
        if current_price <= entry_price * 0.97:
            msg = f"{name} ({ticker}) 損切りしました（-3%）\nエントリー: {entry_price:.2f}, 決済: {current_price:.2f}"
            send_line(msg)
            del positions[ticker]
            return

    else:
        # ゴールデンクロス → 買い
        if prev["SMA5"] < prev["SMA25"] and latest["SMA5"] > latest["SMA25"]:
            entry_price = latest["Close"]
            positions[ticker] = {"entry": entry_price, "status": "long"}
            msg = f"{name} ({ticker}) ゴールデンクロスで買いエントリー\nエントリー価格: {entry_price:.2f}"
            send_line(msg)


# 全銘柄チェック
for code, name in watchlist.items():
    check_signal(code, name)
