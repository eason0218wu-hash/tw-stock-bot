"""
台股盤中選股機器人
------------------
邏輯：
1. 讀取觀察名單（watchlist.txt，一行一個股票代碼；若沒有此檔，預設抓全部上市股票）
2. 批次呼叫 TWSE 即時行情 API，取得每檔的現價、昨收、開盤價、今日累積成交量
3. 依成交量排序，取前 TOP_N 名
4. 對這前 N 名，檢查：
   a. 漲幅 (現價-昨收)/昨收 > PCT_THRESHOLD
   b. 開盤價 > 5 日均線
5. 符合條件、且「今天還沒通知過」的股票 → 發 Telegram 通知

注意：
- TWSE 即時行情為免費公開 API，但沒有官方 SLA，欄位或行為未來可能調整；
  若程式報錯，把錯誤訊息貼給我，我可以協助排查。
- 5 日均線每天只計算一次並快取，避免每次 polling 都打歷史資料 API。
"""

import os
import json
import time
from datetime import datetime

import requests
import twstock

# ------------- 參數設定 -------------
PCT_THRESHOLD = 8.5      # 漲幅門檻 (%)
MA_DAYS = 5                 # 均線天數
TOP_N = 30                  # 成交量排行取前幾名
WATCHLIST_FILE = "watchlist.txt"
STATE_DIR = "state"
CHUNK_SIZE = 80              # 每次呼叫即時行情 API 的股票數量上限（避免網址過長被拒）
SLEEP_BETWEEN_CHUNKS = 0.3   # 每批次呼叫間隔秒數，避免打太快被 TWSE 擋

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TODAY = datetime.now().strftime("%Y%m%d")
STATE_FILE = os.path.join(STATE_DIR, f"alerted_{TODAY}.json")
MA_CACHE_FILE = os.path.join(STATE_DIR, f"ma5_{TODAY}.json")


# ------------- 觀察名單 -------------
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            codes = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        print(f"[info] 從 {WATCHLIST_FILE} 讀取 {len(codes)} 檔觀察名單")
        return codes

    codes = []
    for code, info in twstock.codes.items():
        try:
            if info.type == "股票" and info.market == "上市":
                codes.append(code)
        except AttributeError:
            continue
    print(f"[info] 未找到 {WATCHLIST_FILE}，預設使用全部上市股票，共 {len(codes)} 檔")
    return codes


# ------------- 即時行情 -------------
def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def fetch_realtime(codes):
    """回傳 {code: {"open":..,"latest":..,"prev_close":..,"volume":..}}"""
    result = {}
    for group in chunk(codes, CHUNK_SIZE):
        try:
            raw = twstock.realtime.get(group)
        except Exception as e:
            print(f"[warn] 即時行情抓取失敗（略過此批次）: {e}")
            continue

        # 單檔查詢時 twstock 回傳格式跟多檔查詢不同，這裡統一轉成 {code: info}
        if len(group) == 1:
            raw = {group[0]: raw}

        for code, info in raw.items():
            if not isinstance(info, dict) or not info.get("success"):
                continue
            try:
                rt = info["realtime"]
                open_price = float(rt["open"]) if rt.get("open") else None
                latest = float(rt["latest_trade_price"]) if rt.get("latest_trade_price") else None
                volume = int(rt["accumulate_trade_volume"]) if rt.get("accumulate_trade_volume") else 0
                prev_close = float(info["info"].get("y", 0) or 0) if "info" in info else None
                # 有些版本的 twstock 把昨收放在 realtime 裡，做個保底判斷
                if not prev_close:
                    prev_close = float(rt.get("open", 0) or 0)  # fallback，非最佳解
                if open_price and latest:
                    result[code] = {
                        "name": info.get("info", {}).get("name", code),
                        "open": open_price,
                        "latest": latest,
                        "prev_close": prev_close,
                        "volume": volume,
                    }
            except (KeyError, TypeError, ValueError) as e:
                print(f"[warn] 解析 {code} 即時資料失敗: {e}")
        time.sleep(SLEEP_BETWEEN_CHUNKS)
    return result


# ------------- 5 日均線（每天快取一次）-------------
def load_ma5_cache():
    if os.path.exists(MA_CACHE_FILE):
        with open(MA_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ma5_cache(cache):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(MA_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def get_ma5(codes):
    cache = load_ma5_cache()
    missing = [c for c in codes if c not in cache]

    for code in missing:
        try:
            stock = twstock.Stock(code)          # 建立時會自動抓近期資料
            closes = [c for c in stock.close if c is not None]
            if len(closes) >= MA_DAYS:
                cache[code] = sum(closes[-MA_DAYS:]) / MA_DAYS
        except Exception as e:
            print(f"[warn] 計算 {code} 5日均線失敗: {e}")
        time.sleep(0.1)

    if missing:
        save_ma5_cache(cache)
    return cache


# ------------- 已通知清單（避免同一天重複通知）-------------
def load_alerted():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_alerted(alerted_set):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(alerted_set), f, ensure_ascii=False)


# ------------- Telegram 通知 -------------
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[warn] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，僅印出訊息：")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        if r.status_code != 200:
            print(f"[warn] Telegram 發送失敗: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[warn] Telegram 發送例外: {e}")


# ------------- 主流程 -------------
def main():
    watchlist = load_watchlist()
    if not watchlist:
        print("[error] 觀察名單是空的，結束")
        return

    quotes = fetch_realtime(watchlist)
    if not quotes:
        print("[info] 沒有抓到任何即時報價（可能非交易時間），結束")
        return

    # 依今日累積成交量排序，取前 TOP_N
    ranked = sorted(quotes.items(), key=lambda kv: kv[1]["volume"], reverse=True)[:TOP_N]
    top_codes = [code for code, _ in ranked]
    print(f"[info] 成交量前 {TOP_N} 名: {top_codes}")

    ma5_map = get_ma5(top_codes)
    alerted = load_alerted()

    new_alerts = []
    for code, q in ranked:
        if code in alerted:
            continue
        prev_close = q["prev_close"]
        if not prev_close:
            continue
        pct_change = (q["latest"] - prev_close) / prev_close * 100
        ma5 = ma5_map.get(code)

        if pct_change > PCT_THRESHOLD and ma5 and q["open"] > ma5:
            msg = (
                f"🚀 <b>{q['name']} ({code})</b> 符合進場條件\n"
                f"漲幅: {pct_change:.2f}%（門檻 {PCT_THRESHOLD}%）\n"
                f"開盤價: {q['open']:.2f} / 5日均線: {ma5:.2f}\n"
                f"現價: {q['latest']:.2f}｜今日累積量: {q['volume']:,}\n"
                f"時間: {datetime.now().strftime('%H:%M:%S')}"
            )
            send_telegram(msg)
            new_alerts.append(code)
            alerted.add(code)

    if new_alerts:
        save_alerted(alerted)
        print(f"[info] 本次新通知: {new_alerts}")
    else:
        print("[info] 本次沒有符合條件且尚未通知過的股票")


if __name__ == "__main__":
    main()
