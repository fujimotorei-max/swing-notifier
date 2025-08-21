# swing_notifier.py
import os
import json
import datetime as dt
from typing import Dict, Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf
import pytz

# =========================
# CONFIG
# =========================
# LINE Messaging API token (Actions の Secrets から環境変数で入れる)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

# Watchlist (TSE tickers)
WATCHLIST = {
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
    "8306.T": "三菱UFJフィナンシャルG",
    "8411.T": "みずほFG",
    "8591.T": "オリックス",
    "8750.T": "第一生命HD",
    "8766.T": "東京海上HD",
    "8801.T": "三井不動産",
    "8802.T": "三菱地所",
    "9434.T": "ソフトバンク",
    "9503.T": "関西電力",
    "9613.T": "NTTデータ",
}

# Moving average periods
DAILY_SHORT = 20  # 日足 20MA
DAILY_LONG = 50   # 日足 50MA
INTRA_SHORT = 5   # 30分足 5本
INTRA_LONG = 20   # 30分足 20本

# RSI settings (30分足)
RSI_PERIOD = 14
RSI_BUY_MAX = 40   # 40以下から反発で買い
RSI_SELL_MIN = 60  # 60以上から反落で売り

# Exit settings
TP1 = 0.03  # +3% 到達で利確検討通知
TP2 = 0.05  # +5% 到達で利確検討通知
SWING_LOOKBACK = 20  # 直近高値・安値の探索本数（30分足）
SL_BUFFER = 0.001    # 損切りレベルに対するバッファ(0.1%)

# State management
STATE_PATH = "trade_state.json"  # 保存先（Actionsでは state ブランチへ退避）

# Timezone
JST = pytz.timezone("Asia/Tokyo")


# =========================
# Helpers
# =========================
def now_jst() -> dt.datetime:
    return dt.datetime.now(tz=JST)


def format_ts(ts) -> str:
    """Format timestamp in JST for messages."""
    if isinstance(ts, (pd.Timestamp, np.datetime64)):
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ts = ts.tz_convert(JST)
    elif isinstance(ts, dt.datetime):
        ts = ts.astimezone(JST) if ts.tzinfo else JST.localize(ts)
    return ts.strftime("%Y-%m-%d %H:%M")


def send_line(message: str) -> None:
    """Broadcast a message through LINE Messaging API."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[WARN] CHANNEL_ACCESS_TOKEN is not set. Message not sent:\n", message)
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print("LINE status:", resp.status_code, resp.text[:200])
    except Exception as e:
        print("LINE error:", e)


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Wilder)"""
    delta = series.diff()
    gain = pd.Series(np.where(delta > 0, delta, 0.0), index=series.index)
    loss = pd.Series(np.where(delta < 0, -delta, 0.0), index=series.index)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)


def crossed_up(prev_a: float, prev_b: float, curr_a: float, curr_b: float) -> bool:
    return (prev_a <= prev_b) and (curr_a > curr_b)


def crossed_down(prev_a: float, prev_b: float, curr_a: float, curr_b: float) -> bool:
    return (prev_a >= prev_b) and (curr_a < curr_b)


def recent_swing_low(df: pd.DataFrame, lookback: int) -> float:
    return float(df["Low"].iloc[-lookback:].min()) if len(df) >= lookback else float(df["Low"].min())


def recent_swing_high(df: pd.DataFrame, lookback: int) -> float:
    return float(df["High"].iloc[-lookback:].max()) if len(df) >= lookback else float(df["High"].max())


# =========================
# Data fetching
# =========================
def fetch_daily(tickers):
    # Need at least 50 days for 50MA; grab 1y
    return yf.download(
        tickers=tickers,
        period="1y",
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        threads=True,
        progress=False,
    )


def fetch_intraday(tickers):
    # 30m bars; up to 60d available. Use 30d for speed.
    return yf.download(
        tickers=tickers,
        period="30d",
        interval="30m",
        auto_adjust=True,
        group_by="ticker",
        threads=True,
        progress=False,
    )


def get_last_two(df: pd.DataFrame):
    if len(df) < 2:
        return None, None
    return df.iloc[-2], df.iloc[-1]


# =========================
# Core logic per ticker
# =========================
def evaluate_ticker(
    ticker: str,
    name: str,
    daily_block: pd.DataFrame,
    intra_block: pd.DataFrame,
    state: Dict[str, Any]
):
    # Normalize columns for multi-ticker download
    daily = daily_block[ticker].copy() if isinstance(daily_block.columns, pd.MultiIndex) else daily_block.copy()
    intra = intra_block[ticker].copy() if isinstance(intra_block.columns, pd.MultiIndex) else intra_block.copy()

    daily = daily.dropna(subset=["Close"])
    intra = intra.dropna(subset=["Close"])

    if len(daily) < max(DAILY_LONG + 5, 60) or len(intra) < max(INTRA_SHORT + INTRA_LONG + 5, 50):
        print(f"[SKIP] Not enough data for {ticker}")
        return

    # MAs
    daily["MA_S"] = daily["Close"].rolling(DAILY_SHORT).mean()
    daily["MA_L"] = daily["Close"].rolling(DAILY_LONG).mean()
    intra["MA_S"] = intra["Close"].rolling(INTRA_SHORT).mean()
    intra["MA_L"] = intra["Close"].rolling(INTRA_LONG).mean()
    intra["RSI"] = rsi(intra["Close"], RSI_PERIOD)

    # Trend filter (daily)
    d_prev, d_curr = get_last_two(daily[["MA_S", "MA_L", "Close"]])
    if d_prev is None:
        return
    buy_allowed = d_curr["MA_S"] > d_curr["MA_L"]
    sell_allowed = d_curr["MA_S"] < d_curr["MA_L"]

    # Intraday latest
    i_prev, i_curr = get_last_two(intra[["MA_S", "MA_L", "Close", "RSI"]])
    if i_prev is None:
        return

    last_ts = intra.index[-1]
    ts_str = format_ts(last_ts)

    # State init
    st = state.get(ticker, {
        "open": False,
        "side": None,
        "entry_price": None,
        "entry_time": None,
        "sl": None,
        "tp1_sent": False,
        "tp2_sent": False,
        "last_signal_time": None,
    })

    # Entry signals
    buy_signal = False
    if buy_allowed:
        gc = crossed_up(i_prev["MA_S"], i_prev["MA_L"], i_curr["MA_S"], i_curr["MA_L"])
        above = i_curr["Close"] > i_curr["MA_L"]
        rsi_ok = (i_prev["RSI"] <= RSI_BUY_MAX) and (i_curr["RSI"] > i_prev["RSI"])
        buy_signal = gc and above and rsi_ok

    sell_signal = False
    if sell_allowed:
        dc = crossed_down(i_prev["MA_S"], i_prev["MA_L"], i_curr["MA_S"], i_curr["MA_L"])
        below = i_curr["Close"] < i_curr["MA_L"]
        rsi_ok = (i_prev["RSI"] >= RSI_SELL_MIN) and (i_curr["RSI"] < i_prev["RSI"])
        sell_signal = dc and below and rsi_ok

    same_bar_sent = (st.get("last_signal_time") == str(last_ts))

    if not st["open"]:
        if buy_signal and not same_bar_sent:
            sl = recent_swing_low(intra, SWING_LOOKBACK) * (1 - SL_BUFFER)
            st.update({
                "open": True, "side": "long",
                "entry_price": float(i_curr["Close"]),
                "entry_time": str(last_ts),
                "sl": float(sl),
                "tp1_sent": False, "tp2_sent": False,
                "last_signal_time": str(last_ts),
            })
            msg = (
                f"【買いシグナル】{name}（{ticker}）\n"
                f"時刻: {ts_str}\n条件: GC + 20MA上 + RSI反発\n"
                f"始値目安: {i_curr['Close']:.2f}\n"
                f"損切り: {sl:.2f}（直近安値-0.1%）\n利確目安: +3% / +5%"
            )
            send_line(msg)

        elif sell_signal and not same_bar_sent:
            sh = recent_swing_high(intra, SWING_LOOKBACK) * (1 + SL_BUFFER)
            st.update({
                "open": True, "side": "short",
                "entry_price": float(i_curr["Close"]),
                "entry_time": str(last_ts),
                "sl": float(sh),
                "tp1_sent": False, "tp2_sent": False,
                "last_signal_time": str(last_ts),
            })
            msg = (
                f"【売りシグナル】{name}（{ticker}）\n"
                f"時刻: {ts_str}\n条件: DC + 20MA下 + RSI反落\n"
                f"始値目安: {i_curr['Close']:.2f}\n"
                f"損切り: {sh:.2f}（直近高値+0.1%）\n利確目安: -3% / -5%"
            )
            send_line(msg)

    else:
        price = float(i_curr["Close"])
        exit_cross_long = crossed_down(i_prev["MA_S"], i_prev["MA_L"], i_curr["MA_S"], i_curr["MA_L"])
        exit_cross_short = crossed_up(i_prev["MA_S"], i_prev["MA_L"], i_curr["MA_S"], i_curr["MA_L"])

        if st["side"] == "long":
            if price <= st["sl"]:
                send_line(
                    f"【損切りシグナル】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ロングの損切り目安を下回り\n価格: {price:.2f} ≤ SL: {st['sl']:.2f}"
                )
                st.update({"open": False, "side": None})

            if not st["tp1_sent"] and price >= st["entry_price"] * (1 + TP1):
                st["tp1_sent"] = True
                send_line(
                    f"【利確検討】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ロング +3% 到達\n現在: {price:.2f} / エントリー: {st['entry_price']:.2f}"
                )

            if not st["tp2_sent"] and price >= st["entry_price"] * (1 + TP2):
                st["tp2_sent"] = True
                send_line(
                    f"【利確検討】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ロング +5% 到達\n現在: {price:.2f} / エントリー: {st['entry_price']:.2f}"
                )

            if exit_cross_long and not same_bar_sent:
                st["last_signal_time"] = str(last_ts)
                send_line(
                    f"【手仕舞い候補】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"短期線が再びデッドクロス\n30分足 5MA↓20MA"
                )

        elif st["side"] == "short":
            if price >= st["sl"]:
                send_line(
                    f"【損切りシグナル】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ショートの損切り目安を上回り\n価格: {price:.2f} ≥ SL: {st['sl']:.2f}"
                )
                st.update({"open": False, "side": None})

            if not st["tp1_sent"] and price <= st["entry_price"] * (1 - TP1):
                st["tp1_sent"] = True
                send_line(
                    f"【利確検討】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ショート -3% 到達\n現在: {price:.2f} / エントリー: {st['entry_price']:.2f}"
                )

            if not st["tp2_sent"] and price <= st["entry_price"] * (1 - TP2):
                st["tp2_sent"] = True
                send_line(
                    f"【利確検討】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"ショート -5% 到達\n現在: {price:.2f} / エントリー: {st['entry_price']:.2f}"
                )

            if exit_cross_short and not same_bar_sent:
                st["last_signal_time"] = str(last_ts)
                send_line(
                    f"【手仕舞い候補】{name}（{ticker}）\n時刻: {ts_str}\n"
                    f"短期線が再びゴールデンクロス\n30分足 5MA↑20MA"
                )

    state[ticker] = st


# =========================
# Main
# =========================
def main():
    start = now_jst()
    print(f"[{start.strftime('%Y-%m-%d %H:%M:%S')}] Scan start")

    tickers = list(WATCHLIST.keys())
    daily_df = fetch_daily(tickers)
    intra_df = fetch_intraday(tickers)

    state = load_state()

    for t in tickers:
        try:
            evaluate_ticker(t, WATCHLIST[t], daily_df, intra_df, state)
        except Exception as e:
            print(f"[ERROR] {t}: {e}")

    save_state(state)
    end = now_jst()
    print(f"[{end.strftime('%Y-%m-%d %H:%M:%S')}] Scan end")


if __name__ == "__main__":
    main()
