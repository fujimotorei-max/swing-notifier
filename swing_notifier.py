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
WATCH_LIMIT = int(os.environ.get("WATCH_LIMIT", "150"))   # 1回の実行で見る最大銘柄数
TIME_SLEEP_MS = int(os.environ.get("TIME_SLEEP_MS", "120"))  # yfinance連打抑制

# ===== LINE =====
def send_line(message: str):
    """LINEブロードキャスト（トークン未設定ならスキップ）"""
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
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
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

# ===== 30分足→日足合成（必要なら使う） =====
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

    daily["SMA5"]  = daily["Close"].rolling(5).mean()
    daily["SMA25"] = daily["Close"].rolling(25).mean()
    daily["SMA75"] = daily["Close"].rolling(75).mean()
    return daily.dropna()

# ===== ウォッチ銘柄 =====
# ★ ここは今の巨大 watchlist をそのまま貼り付けてください
from watchlist_module import watchlist  # ←例：外出ししてもOK。直接変数でもOK。

# ===== メイン =====
def run(mode="intraday"):
    assert mode in ("daily", "intraday"), "mode must be 'daily' or 'intraday'"
    state = load_state()

    # 手動リセット（intraday のときだけ）
    if mode == "intraday":
        reset_cmd = load_reset()
        if reset_cmd:
            done = []
            for code in list(reset_cmd.keys()):
                state[code] = {"status": "NONE"}
                done.append(code)
            if done:
                send_line("🔄 手動リセット: " + ", ".join(done))
            clear_reset()

    # ウォッチ数の制限（レート対策）
    items = list(watchlist.items())[:WATCH_LIMIT]

    for idx, (code, name) in enumerate(items, 1):
        print(f"\n=== [{idx}/{len(items)}] {code} {name} ===")
        tstate = state.get(code, {"status": "NONE"})

        try:
            if mode == "daily":
                # 1dを長めに取得
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

                price = float(daily["Close"].iloc[-1])
                prev  = daily.iloc[-2]
                curr  = daily.iloc[-1]

                # ゴールデンクロス＆整列の判定
                gcross = (prev["SMA5"] <= prev["SMA25"]) and (curr["SMA5"] > curr["SMA25"])
                prev_align = (prev["SMA5"] > prev["SMA25"] > prev["SMA75"])
                curr_align = (curr["SMA5"] > curr["SMA25"] > curr["SMA75"])
                align_new  = (not prev_align) and curr_align

                if tstate.get("status") == "NONE" and (gcross or align_new):
                    sl = price * 0.97
                    tp = price * 1.06
                    reason = "ゴールデンクロス" if gcross else "短期>中期>長期の整列"
                    send_line(
                        f"⚡️【{name}({code})】日足エントリーシグナル（{reason}）\n"
                        f"終値: {price:.0f}円\n"
                        f"📈 利確ライン: {tp:.0f}円\n"
                        f"📉 損切りライン: {sl:.0f}円\n"
                        f"👉 OCO注文をセットしてください"
                    )
                    tstate = {"status": "HOLD", "entry_price": float(price)}

                state[code] = tstate

            else:  # intraday
                # 場中は利確/損切りの到達監視のみ
                if tstate.get("status") == "HOLD":
                    df = yf.download(code, period="5d", interval="30m", progress=False)
                    if df is None or df.empty:
                        print("データなし")
                        state[code] = tstate
                        continue

                    entry = float(tstate["entry_price"])
                    sl = entry * 0.97
                    tp = entry * 1.06

                    # 直近バーで判定（必要なら当日分に絞るなど拡張可）
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
                else:
                    # HOLDでないなら何もしない
                    state[code] = tstate

        except Exception as e:
            print(f"[ERROR] {code} 処理中に例外:", e)
            # 例外時も状態は維持
            state[code] = tstate

        # 都度保存（途中で止まっても被害最小化）
        save_state(state)
        # 軽くスリープしてレート緩和
        if TIME_SLEEP_MS > 0:
            time.sleep(TIME_SLEEP_MS / 1000.0)

    # 念のため最後にも保存
    save_state(state)

if __name__ == "__main__":
    run(mode=os.environ.get("RUN_MODE", "intraday"))
