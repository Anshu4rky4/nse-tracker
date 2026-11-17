"""Daily company news for symbols.txt -> docs/data/news/YYYY-MM-DD.json
Gemini sirf background me headlines filter karta hai (website par uska analysis/link nahi jata)."""
import json, os, re, sys, time, urllib.parse
from datetime import datetime, timedelta, timezone
import feedparser, requests
from fetch_nse import symbols, load_catalysts, IST, DATA

# =============== CONFIG ===============
NEWS_DIR = os.path.join(DATA, "news")
HOURS_BACK = 36
MAX_PER_SYMBOL = 20          # RSS se max entries per symbol
ANALYZE_TOP = 12             # Gemini ko har symbol ki itni headlines
MAX_SHOW_PER_SYMBOL = 5      # website par max headlines per symbol
FETCH_DELAY_SEC = 0.5
INCLUDE_RESULT_NEWS = True   # kal result wale symbols (resultcalender.csv) ki result headlines bhi
RESULT_HEADLINES = 3

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")   # GitHub secret se; code me kabhi hardcode mat karna
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_DELAY_SEC = 6
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_WAIT_SEC = 25
GEMINI_MAX_SYMBOLS_PER_CALL = 15

ULTRA_SEARCH_WHEN = "2d"
ULTRA_SEARCH_SUFFIX = (" (announces OR launches OR order OR acquisition OR approval OR stake "
                       "OR fundraise OR resigns OR USFDA OR contract OR agreement OR plant)")
RESULT_QUERY_SUFFIX = " quarterly results net profit revenue"
ULTRA_EXCLUDED_KEYWORDS = [
    "target price", "price target", "brokerage", "analyst", "analysts", "stocks to watch",
    "stocks to buy", "buy or sell", "should you buy", "buy rating", "sell rating", "top picks",
    "technical", "outlook", "nifty", "sensex", "intraday", "gainers", "losers",
    "opening bell", "closing bell", "buzzing stocks", "share price today", "expert", "view on",
]
RESULT_EXCLUDED_KEYWORDS = [
    "stocks to watch today", "watch today", "buy", "sell or hold", "pre-market", "breakout",
    "technical", "breakdown", "technical analysis", "open interest", "gainer", "loser",
    "opening bell", "closing bell", "sensex", "nifty", "intraday", "buzzing stocks",
]
LOW_QUALITY_SOURCES = ["linkedin", "biggo", "ad hoc news", "stockstotrade",
                       "upstox", "tradingview", "facebook", "reddit"]
SEARCH_QUERY_OVERRIDE = {
    "BSE": "BSE Ltd share", "PERSISTENT": "Persistent Systems", "ITC": "ITC Ltd share",
    "IOC": "Indian Oil Corporation", "LT": "Larsen & Toubro", "ABB": "ABB India",
    "UBL": "United Breweries", "JSL": "Jindal Stainless", "IGL": "Indraprastha Gas",
    "TMPV": "Tata Motors Passenger Vehicles", "M&M": "Mahindra & Mahindra",
    "TIINDIA": "Tube Investments of India", "VBL": "Varun Beverages",
    "LTIM": "LTIMindtree", "OFSS": "Oracle Financial Services Software",
    "SAIL": "Steel Authority of India SAIL", "GLAND": "Gland Pharma",
    "RADICO": "Radico Khaitan", "PFC": "Power Finance Corporation",
}
# =====================================

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
ULTRA_PROMPT = """You are an expert Indian stock market (NSE) intraday news filter.
Below are news headlines grouped by stock symbol. For EACH symbol, pick ONLY headlines that are
ULTRA HIGH IMPACT, ACTUAL COMPANY NEWS (company announcement / exchange filing / company-originated event).

ULTRA_HIGH categories: ORDER_WIN (big order/contract), M&A (merger, acquisition, demerger, stake sale/purchase, JV),
DEAL (bulk/block deal, promoter or big investor buying/selling), FUNDRAISE (QIP, rights, preferential, buyback,
dividend, bonus, split), REGULATORY (USFDA, SEBI/CCI/ED/CBI/tax action, licence), MANAGEMENT (CEO/MD/CFO/promoter
change), RISK (fraud, raid, default, fire/plant shutdown, rating change), LAUNCH (major product/drug/plant/capacity),
GUIDANCE (big capex, guidance change).

REJECT: analyst/brokerage opinions, target prices, buy-sell-hold calls, stock tips, technical views, market wraps,
"stocks to watch", price moves without a real company event, interviews/opinion pieces, quarterly result headlines,
routine items (AGM notice, board meeting intimation, CSR, awards, small orders), other companies (name collision).

Rules: Use ONLY the headlines. Do not invent anything. If unsure, EXCLUDE. If nothing qualifies, events is empty.
Return ONLY a JSON array, one object per symbol:
[{"id": 0, "events": [{"headline_index": 2}]}]

SYMBOLS AND HEADLINES:
"""


def kw_re(words):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(k.lower()) for k in words) + r")(?![a-z0-9])")


ULTRA_RE, RESULT_RE = kw_re(ULTRA_EXCLUDED_KEYWORDS), kw_re(RESULT_EXCLUDED_KEYWORDS)


def call_gemini(prompt):
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                print(f"   [Gemini] {r.status_code} - retry {attempt}/{GEMINI_MAX_RETRIES}")
                time.sleep(GEMINI_RETRY_WAIT_SEC); continue
            if r.status_code != 200:
                print(f"   [Gemini] Error {r.status_code}: {r.text[:200]}"); return None
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip())
        except Exception as e:
            print(f"   [Gemini] Exception: {e} (attempt {attempt}/{GEMINI_MAX_RETRIES})")
            time.sleep(GEMINI_RETRY_WAIT_SEC)
    return None


def fetch_news(query, excl_re, cutoff):
    feed = feedparser.parse("https://news.google.com/rss/search?q=" + urllib.parse.quote(query))
    out, seen = [], set()
    for e in feed.entries[:MAX_PER_SYMBOL]:
        if not getattr(e, "published_parsed", None): continue
        pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        if pub < cutoff: continue
        full = re.sub("<.*?>", "", e.title).strip()
        title, source = (" - ".join(full.split(" - ")[:-1]).strip(), full.split(" - ")[-1].strip()) if " - " in full else (full, "")
        if excl_re.search(title.lower()) or any(s in source.lower() for s in LOW_QUALITY_SOURCES): continue
        k = re.sub(r"\W+", " ", title.lower()).strip()
        if k in seen: continue
        seen.add(k)
        out.append({"h": title, "s": source, "p": pub.astimezone(IST).strftime("%Y-%m-%dT%H:%M")})
    return sorted(out, key=lambda x: x["p"], reverse=True)


def ultra_filter(cands):
    """cands: {sym: [news]} -> Gemini ne jo ULTRA_HIGH chune sirf wahi (background filter)."""
    picked, syms = {}, list(cands)
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY nahi mila - company news skip"); return picked
    for st in range(0, len(syms), GEMINI_MAX_SYMBOLS_PER_CALL):
        chunk, lines = syms[st:st + GEMINI_MAX_SYMBOLS_PER_CALL], []
        for i, s in enumerate(chunk):
            lines.append(f"{i}. SYMBOL: {s}")
            lines += [f"   [{j}] {n['h']} (Source: {n['s']})" for j, n in enumerate(cands[s][:ANALYZE_TOP])]
        parsed = call_gemini(ULTRA_PROMPT + "\n".join(lines)); time.sleep(GEMINI_DELAY_SEC)
        for p in parsed if isinstance(parsed, list) else []:
            try: s = chunk[int(p["id"])]
            except (KeyError, ValueError, IndexError, TypeError): continue
            for ev in p.get("events") or []:
                try: picked.setdefault(s, []).append(cands[s][int(ev["headline_index"])])
                except (KeyError, ValueError, IndexError, TypeError): pass
    return picked


def main():
    now = datetime.now(IST)
    mine = symbols()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    cands = {}
    for s in mine:
        q = SEARCH_QUERY_OVERRIDE.get(s, s)
        try: n = fetch_news(f"{q}{ULTRA_SEARCH_SUFFIX} when:{ULTRA_SEARCH_WHEN}", ULTRA_RE, cutoff)
        except Exception as e: print("RSS error", s, e); n = []
        if n: cands[s] = n
        time.sleep(FETCH_DELAY_SEC)
    news = ultra_filter(cands)

    if INCLUDE_RESULT_NEWS:
        cr = load_catalysts(set(mine), now.date())
        for s in sorted((cr[1] if cr else {})):
            try: rn = fetch_news(SEARCH_QUERY_OVERRIDE.get(s, s) + RESULT_QUERY_SUFFIX, RESULT_RE, cutoff)[:RESULT_HEADLINES]
            except Exception as e: print("RSS error", s, e); rn = []
            have = {x["h"] for x in news.get(s, [])}
            news[s] = [dict(x, r=1) for x in rn if x["h"] not in have] + news.get(s, [])
            time.sleep(FETCH_DELAY_SEC)

    news = {s: sorted(v, key=lambda x: x["p"], reverse=True)[:MAX_SHOW_PER_SYMBOL] for s, v in news.items() if v}
    os.makedirs(NEWS_DIR, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    json.dump({"date": day, "updated": now.strftime("%H:%M:%S"), "news": news},
              open(os.path.join(NEWS_DIR, day + ".json"), "w"), separators=(",", ":"), ensure_ascii=False)
    print("saved news", day, "| symbols:", len(news), "| headlines:", sum(len(v) for v in news.values()))


if __name__ == "__main__":
    try: main()
    except Exception as e: print("ERROR", e); sys.exit(1)
