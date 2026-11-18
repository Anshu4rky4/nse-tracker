"""symbols.txt ke symbols ke NSE corporate announcements -> Gemini (ultra high impact filter + short Hindi summary)
-> docs/data/ann/YYYY-MM-DD.json.  Manual run, din me ek baar:  python fetch_announcements.py
Window: run-date + pichhla din (18 Nov run -> 18 + 17 Nov). Monday run -> Mon + Sun + Sat + Fri.
Test/backfill: RUN_DATE=2026-11-18 python fetch_announcements.py"""
import json, os, re, sys, time
from datetime import datetime, timedelta
import requests
from fetch_nse import symbols, IST, DATA, HDR

# =============== CONFIG ===============
ANN_DIR = os.path.join(DATA, "ann")
NSE_BASE = "https://www.nseindia.in"
ANN_URL = NSE_BASE + "/api/corporate-announcements?index=equities&symbol={sym}"
FETCH_DELAY_SEC = 0.6
MAX_TEXT_CHARS = 1500        # Gemini ko har announcement ka itna voiceSummary
# in 'desc' wale routine announcements Gemini ko bheje hi nahi jaate (kharcha bachane ke liye)
EXCLUDED_DESC = [
    "newspaper publication", "analysts/institutional investor meet", "trading window",
    "compliance certificate", "reg. 74", "loss of share certificates", "duplicate share",
    "change in address", "registrar", "schedule of analyst",
]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")   # export GEMINI_API_KEY=... (code me hardcode mat karna)
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_DELAY_SEC = 6
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_WAIT_SEC = 25
GEMINI_BATCH = 10            # ek Gemini call me itne announcements

# ---- YAHAN APNA ULTRA HIGH CATALYST PROMPT / CATEGORIES BADALNA ----
ULTRA_CATEGORIES = ("RESULT, MERGER, DEMERGER, ACQUISITION, RESTRUCTURING, ORDER_WIN, FUNDRAISE (QIP/rights/preferential/buyback), "
                    "DIVIDEND_BONUS_SPLIT, REGULATORY (USFDA/SEBI/NCLT/tax/licence), MANAGEMENT (CEO/MD/CFO/promoter change), "
                    "CAPEX_EXPANSION (big plant/capacity), RATING_CHANGE, RISK (fraud/default/fire/shutdown)")
PROMPT = """You are an expert Indian stock market (NSE) analyst. Below are NSE corporate announcements.
For EACH one decide if it is ULTRA HIGH IMPACT for the stock price. ULTRA HIGH categories: """ + ULTRA_CATEGORIES + """.
NOT ultra high (ultra=false): routine compliance, newspaper publications, investor-call transcripts or schedules, trading window,
certificates, AGM/board-meeting intimations, minor/small orders, ESOP allotments, any general update.
Rules: Use ONLY the given text. Do not invent numbers. If unsure, ultra=false.
For ultra=true give: category (one of the above, short word) and summary_hi = VERY SHORT Hinglish summary (Hindi language written in ENGLISH/Roman letters, NOT Devanagari script,
like "Prestige Estates ne Prestige Vaishnoi Projects me 4.59% extra hissedari Rs 110.9 cr me kharidi"),
max 25 words, keep key numbers (amount, % growth, ratio). Never use Devanagari characters. For ultra=false: category "" and summary_hi "".
Return ONLY a JSON array: [{"id": 0, "ultra": true, "category": "RESULT", "summary_hi": "..."}]

ANNOUNCEMENTS:
"""
# =====================================
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def call_gemini(prompt):
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for a in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                print(f"   [Gemini] {r.status_code} - retry {a}/{GEMINI_MAX_RETRIES}"); time.sleep(GEMINI_RETRY_WAIT_SEC); continue
            if r.status_code != 200:
                print(f"   [Gemini] Error {r.status_code}: {r.text[:200]}"); return None
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.MULTILINE).strip())
        except Exception as e:
            print(f"   [Gemini] Exception: {e} (attempt {a})"); time.sleep(GEMINI_RETRY_WAIT_SEC)
    return None


def fetch_ann(s, sym):
    for i in range(3):
        try:
            r = s.get(ANN_URL.format(sym=requests.utils.quote(sym)), headers=HDR, timeout=25)
            if r.status_code == 200 and r.text.strip():
                js = r.json()
                return js.get("data", []) if isinstance(js, dict) else js
        except (requests.RequestException, ValueError) as e:
            print("   retry", sym, i, e)
        time.sleep(3); s.get(NSE_BASE, headers=HDR, timeout=25)
    print("   NSE fetch failed:", sym); return []


def window(run):
    back = 3 if run.weekday() == 0 else 1   # Monday: Mon+Sun+Sat+Fri, baaki din: aaj + kal
    return {run - timedelta(days=i) for i in range(0, back + 1)}


def main():
    run = datetime.strptime(os.getenv("RUN_DATE"), "%Y-%m-%d").date() if os.getenv("RUN_DATE") else datetime.now(IST).date()
    ok_days, mine = window(run), symbols()
    s = requests.Session(); s.get(NSE_BASE, headers=HDR, timeout=25)
    items = []
    for sym in mine:
        for a in fetch_ann(s, sym):
            try: dt = datetime.strptime(a.get("an_dt", ""), "%d-%b-%Y %H:%M:%S")
            except ValueError: continue
            desc = a.get("desc") or ""
            if a.get("symbol", "").upper() != sym or dt.date() not in ok_days: continue
            if any(x in desc.lower() for x in EXCLUDED_DESC): continue
            txt = (a.get("voiceSummary") or a.get("attchmntText") or "")[:MAX_TEXT_CHARS]
            items.append({"sym": sym, "t": dt.strftime("%Y-%m-%dT%H:%M"), "d": desc, "txt": txt})
        time.sleep(FETCH_DELAY_SEC)
    items.sort(key=lambda x: x["t"], reverse=True)
    print(f"window {sorted(map(str, ok_days))} | symbols {len(mine)} | candidates {len(items)}")

    out = {}
    if not GEMINI_API_KEY: print("GEMINI_API_KEY nahi mila - kuch save nahi hoga"); return
    for st in range(0, len(items), GEMINI_BATCH):
        chunk = items[st:st + GEMINI_BATCH]
        body = "\n".join(f"{i}. SYMBOL: {x['sym']} | TYPE: {x['d']}\n   TEXT: {x['txt']}" for i, x in enumerate(chunk))
        parsed = call_gemini(PROMPT + body); time.sleep(GEMINI_DELAY_SEC)
        for p in parsed if isinstance(parsed, list) else []:
            try: x = chunk[int(p["id"])]
            except (KeyError, ValueError, IndexError, TypeError): continue
            if p.get("ultra") is True and p.get("summary_hi"):
                out.setdefault(x["sym"], []).append({"t": x["t"], "d": x["d"], "c": str(p.get("category", "")).upper(),
                                                     "s": p["summary_hi"]})
    day = datetime.now(IST).strftime("%Y-%m-%d") if not os.getenv("RUN_DATE") else os.getenv("RUN_DATE")
    os.makedirs(ANN_DIR, exist_ok=True)
    json.dump({"date": day, "window": sorted(map(str, ok_days)), "updated": datetime.now(IST).strftime("%H:%M:%S"),
               "stats": {"candidates": len(items), "ultra": sum(len(v) for v in out.values())}, "ann": out},
              open(os.path.join(ANN_DIR, day + ".json"), "w"), separators=(",", ":"), ensure_ascii=False)
    print("saved ann", day, "| symbols:", len(out), "| ultra:", sum(len(v) for v in out.values()))


if __name__ == "__main__":
    try: main()
    except Exception as e: print("ERROR", e); sys.exit(1)
