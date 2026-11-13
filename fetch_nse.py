"""NSE F&O gainers/losers rank tracker. Run every 5 min; stores rank history in docs/data/."""
import csv, io, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
import requests

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "docs", "data")
TOP_N = 20
CSV_52W, CSV_RESULT = "52w_ath_scan.csv", "resultcalender.csv"   # daily replace hone wali files (root / docs / docs/data me)
NEAR_PCT = 3.0            # sirf fallback: section column samajh na aaye to % columns se near check
RESULT_KEYWORD = "result" # PURPOSE me ye word ho to hi RESULT catalyst
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

def find_file(name):
    for d in (os.path.join(ROOT, "output"), ROOT, os.path.join(ROOT, "docs"), DATA):
        p = os.path.join(d, name)
        if os.path.exists(p): return p

def read_csv(path):  # BOM + "SYMBOL \n" jaise messy headers saaf karta hai
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows: return []
    head = [re.sub(r"\s+", "", h).lower() for h in rows[0]]
    return [dict(zip(head, [c.strip() for c in r])) for r in rows[1:] if any(c.strip() for c in r)]

def tags_52w(r):
    sec = r.get("section", "").lower()
    ath = re.search(r"(^|[^a-z])ath([^a-z]|$)|all[\s_-]*time", sec)
    sec = re.sub(r"all[\s_-]*time[\s_-]*(high|low)?", " ", sec)
    t = (["ATH"] if ath else []) + (["52W HIGH"] if "high" in sec else []) + (["52W LOW"] if "low" in sec else [])
    if not t:  # fallback: % columns
        for col, tag in (("pct_below_52w_high", "52W HIGH"), ("pct_above_52w_low", "52W LOW"), ("pct_below_ath", "ATH")):
            v = num(r.get(col))
            if v is not None and abs(v) <= NEAR_PCT: t.append(tag)
    return t

def load_catalysts(mine, today):
    """52W High/Low, ATH, Result -> ({sym: [tags]}, {sym: result_date}). Files na mile to None."""
    cat, res = {}, {}
    p52, pres = find_file(CSV_52W), find_file(CSV_RESULT)
    if not (p52 or pres): return None
    if p52:
        for r in read_csv(p52):
            sym = r.get("symbol", "").upper()
            if sym in mine:
                for t in tags_52w(r):
                    if t not in cat.setdefault(sym, []): cat[sym].append(t)
    if pres:
        back = 3 if today.weekday() == 0 else 1   # Monday: Fri+Sat+Sun, baaki din: sirf kal (aaj ka result nahi)
        ok = {today - timedelta(days=i) for i in range(1, back + 1)}
        for r in read_csv(pres):
            sym = r.get("symbol", "").upper()
            if sym not in mine or RESULT_KEYWORD not in r.get("purpose", "").lower(): continue
            try: d = datetime.strptime(r.get("date", ""), "%d-%b-%Y").date()
            except ValueError: continue
            if d in ok and d >= res.get(sym, d): res[sym] = d
        for sym, d in res.items():
            cat.setdefault(sym, []).append("RESULT")
        res = {k: v.strftime("%d-%b-%Y") for k, v in res.items()}
    order = ["52W HIGH", "52W LOW", "ATH", "RESULT"]
    return {k: sorted(v, key=order.index) for k, v in cat.items()}, res

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
    cr = load_catalysts(mine, now.date())   # catalyst us din ke JSON me freeze (backtest ke liye)
    if cr: doc["cat"], doc["res"] = cr
    json.dump(doc, open(path, "w"), separators=(",", ":"))
    days = sorted(f[:-5] for f in os.listdir(DATA) if f[:2] == "20")
    json.dump(days, open(os.path.join(DATA, "index.json"), "w"))
    print("saved", day, snap["t"], "ADU:", adu, "| mere symbols:", len(m), "| catalyst:", len(doc.get("cat", {})))

if __name__ == "__main__":
    try: main()
    except Exception as e: print("ERROR", e); sys.exit(1)