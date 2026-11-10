import os, time, random, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tvDatafeed import TvDatafeed, Interval

# apni puraani poori list yahin paste karo
stock_symbols = ["360ONE","ABB","APLAPOLLO"]  # ... baaki symbols

EXCHANGE    = 'NSE'
MAX_WORKERS = 3
MAX_RETRIES = 3
FETCH_ROUNDS = 3
N_BARS      = 5000          # poori history (ATH ke liye)
WEEK52_BARS = 52
PCT         = 5.0
CSV_PATH    = 'output/52w_ath_scan.csv'   # har run pe replace hoti hai

TV_SYMBOL_MAP = {'BAJAJ-AUTO': 'BAJAJ_AUTO', 'M&M': 'M_M'}
COLUMNS = ['section', 'symbol', 'cmp', 'week52_high', 'week52_low', 'ath',
           'pct_below_52w_high', 'pct_above_52w_low', 'pct_below_ath']

_local = threading.local()

def get_tv():
    if getattr(_local, 'tv', None) is None:
        _local.tv = TvDatafeed()
    return _local.tv

def fetch_symbol(sym):
    for _ in range(MAX_RETRIES):
        try:
            df = get_tv().get_hist(symbol=TV_SYMBOL_MAP.get(sym, sym), exchange=EXCHANGE,
                                   interval=Interval.in_weekly, n_bars=N_BARS)
            if df is not None and not df.empty:
                return sym, df
            _local.tv = None
        except Exception:
            _local.tv = None
        time.sleep(random.uniform(3, 6))
    return sym, None

def fetch_all(symbols):
    data, missing = {}, list(symbols)
    for rnd in range(1, FETCH_ROUNDS + 1):
        if not missing:
            break
        failed = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for f in as_completed([ex.submit(fetch_symbol, s) for s in missing]):
                s, df = f.result()
                if df is not None:
                    data[s] = df
                else:
                    failed.append(s)
        missing = failed
        print(f"Round {rnd}: mila {len(data)}, baaki {len(missing)}")
        if missing:
            time.sleep(10)
    return data, missing

def scan(data):
    rows = []
    for sym, df in data.items():
        cmp_ = df.iloc[-1]['close']
        d52  = df.tail(WEEK52_BARS)
        h52  = max(d52['high'].max(), cmp_)
        l52  = min(d52['low'].min(), cmp_)
        ath  = max(df['high'].max(), cmp_)

        below_h52 = (h52 - cmp_) / h52 * 100
        above_l52 = (cmp_ - l52) / l52 * 100
        below_ath = (ath - cmp_) / ath * 100

        base = {'symbol': sym, 'cmp': round(cmp_, 2),
                'week52_high': round(h52, 2), 'week52_low': round(l52, 2),
                'ath': round(ath, 2)}
        if 0 <= below_h52 <= PCT:
            rows.append({'section': 'NEAR_52W_HIGH', **base, 'pct_below_52w_high': round(below_h52, 2)})
        if 0 <= above_l52 <= PCT:
            rows.append({'section': 'NEAR_52W_LOW', **base, 'pct_above_52w_low': round(above_l52, 2)})
        if 0 <= below_ath <= PCT:
            rows.append({'section': 'NEAR_ATH', **base, 'pct_below_ath': round(below_ath, 2)})
    return rows

def main():
    data, failed = fetch_all(stock_symbols)
    if failed:
        print(f"Failed ({len(failed)}):", failed)
    rows = scan(data)
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    df = pd.DataFrame(rows).reindex(columns=COLUMNS)
    df.to_csv(CSV_PATH, index=False)   # purani file overwrite
    print(f"CSV saved: {CSV_PATH} ({len(df)} rows)")

if __name__ == "__main__":
    main()