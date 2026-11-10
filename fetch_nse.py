"""NSE F&O gainers/losers rank tracker. Run every 5 min; stores rank history in docs/data/."""
import csv, io, json, os, sys, time
from datetime import datetime, timezone, timedelta
import requests

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "docs", "data")
TOP_N = 20
BASE = "https://www.nseindia.com"
URLS = {  # purani API: sirf gainers/losers rank ke liye
    "g": BASE + "/api/live-analysis-variations?index=gainers&type=FOSec&csv=true",
    "l": BASE + "/api/live-analysis-variations?index=loosers&type=FOSec&csv=true",
}
MARKET_URL = (BASE + "/api/NextApi/apiClient/marketWatchApi"
              "?functionName=getIndicesData&symbol=SECURITIES%20IN%20F%26O")
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/market-data/top-gainers-losers",
}

def symbols():
    with open(os.path.join(ROOT, "symbols.txt")) as f:
        return [l.strip().upper() for l in f if l.strip() and not l.startswith("#")]

def num(v):
    try: return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError: return None

def fetch(s, url, as_json=False):
    for i in range(3):
        try:
            r = s.get(url, headers=HDR, timeout=25)
            if r.status_code == 200 and r.text.strip():
                return r.json() if as_json else r.text
        except (requests.RequestException, ValueError) as e:
            print("retry", i, e)
        time.sleep(3)
        s.get(BASE, headers=HDR, timeout=25)  # refresh cookies
    raise RuntimeError("NSE fetch failed: " + url)

def parse(text):  # purana gainers/losers CSV parser (as it is)
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    head = [h.strip().upper() for h in rows[0]]
    def col(*names):
        for n in names:
            for i, h in enumerate(head):
                if n in h: return i
    si, li, pi = col("SYMBOL"), col("LTP"), col("%CHNG", "%CHG", "PERCENT")
    out = {}
    for rank, r in enumerate(rows[1:], 1):
        if len(r) <= si or not r[si].strip(): continue
        out[r[si].strip().upper()] = {"r": rank, "ltp": num(r[li]) if li is not None else None,
                                      "p": num(r[pi]) if pi is not None else None}
    return out

def parse_market(js, mine):
    d = js["data"]
    adu = d.get("aduCount", {})
    m = {}
    for x in d.get("data", []):
        sym = str(x.get("symbol", "")).strip().upper()
        if sym in mine:
            m[sym] = {
                "ltp": x.get("lastPrice"),
                "p":   x.get("pChange"),
                "lut": x.get("lastUpdateTime"),
            }
    return m, {"adv": adu.get("advances"), "dec": adu.get("declines"), "unch": adu.get("unchange")}

def main():
    now = datetime.now(IST)
    if os.getenv("FORCE") != "1" and (now.weekday() > 4 or not (9 * 60 + 10 <= now.hour * 60 + now.minute <= 15 * 60 + 35)):
        print("Market closed, skipping"); return
    s = requests.Session(); s.get(BASE, headers=HDR, timeout=25)
    mine, snap = symbols(), {"t": now.strftime("%H:%M")}

    # 1) Purani API: gainers/losers rank (as it is)
    ranks = {}
    for side, url in URLS.items():
        full = parse(fetch(s, url))
        snap["n" + side] = len(full)
        snap[side] = {k: v for k, v in full.items() if k in mine}
        ranks[side] = {k: v["r"] for k, v in full.items()}

    # 2) Nayi API: LTP, %change + advances/declines/unchanged
    m, adu = parse_market(fetch(s, MARKET_URL, as_json=True), mine)
    for sym, row in m.items():
        gr, lr = ranks["g"].get(sym), ranks["l"].get(sym)
        row["gr"], row["lr"] = gr, lr                       # gainer/loser rank (None = list me nahi)
        row["top_g"] = gr is not None and gr <= TOP_N       # top 20 gainers me?
        row["top_l"] = lr is not None and lr <= TOP_N       # top 20 losers me?
    snap["m"] = m
    snap.update(adu)

    os.makedirs(DATA, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    path = os.path.join(DATA, day + ".json")
    doc = json.load(open(path)) if os.path.exists(path) else {"date": day, "snaps": []}
    doc.update(top_n=TOP_N, symbols=mine, updated=now.strftime("%H:%M:%S"))
    doc["snaps"] = [x for x in doc["snaps"] if x["t"] != snap["t"]] + [snap]
    json.dump(doc, open(path, "w"), separators=(",", ":"))
    days = sorted(f[:-5] for f in os.listdir(DATA) if f[:2] == "20")
    json.dump(days, open(os.path.join(DATA, "index.json"), "w"))
    print("saved", day, snap["t"], "ADU:", adu, "| mere symbols:", len(m))

if __name__ == "__main__":
    try: main()
    except Exception as e: print("ERROR", e); sys.exit(1)