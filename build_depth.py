"""Depth CSVs (private repo) -> docs/data/depth/YYYY-MM-DD.json
Latest date ke har CSV ko pichhle din ke LAST CSV se compare karta hai (% change), sirf symbols.txt wale symbols."""
import csv, glob, json, os, re

# ================= CONFIG =================
SRC_DIR = "depthsrc/depth_data"          # workflow private repo yahin checkout karta hai
OUT_DIR = os.path.join("docs", "data", "depth")
SYMBOLS_FILE = "symbols.txt"
# ==========================================

ROOT = os.path.dirname(os.path.abspath(__file__))
FN = re.compile(r"depth_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.csv$")

def symbols():
    with open(os.path.join(ROOT, SYMBOLS_FILE)) as f:
        return [l.strip().upper() for l in f if l.strip() and not l.startswith("#")]

def num(v):
    try: return float(str(v).replace(",", "").strip())
    except ValueError: return None

def load(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            s = r.get("symbol", "").upper()
            if s: out[s] = (num(r.get("total_buy_qty")), num(r.get("total_sell_qty")))
    return out

def pct(c, b):
    if c is None or b is None or b == 0: return None
    return round((c - b) / b * 100, 2)

def main():
    days = {}
    for p in glob.glob(os.path.join(ROOT, SRC_DIR, "depth_*.csv")):
        m = FN.search(os.path.basename(p))
        if m: days.setdefault(m.group(1), []).append(("".join(m.groups()[1:]), m.group(2) + ":" + m.group(3), p))
    if not days:
        print("depth CSV nahi mili"); return
    target = max(days)
    prevs = [d for d in days if d < target]
    if not prevs:
        print("pichhle din ki CSV nahi mili, baseline nahi bana"); return
    base_day = max(prevs)
    _, base_time, base_path = sorted(days[base_day])[-1]
    base = load(base_path)
    mine = set(symbols())
    sym = {s: {"bp": [], "sp": []} for s in mine if s in base}
    times, last = [], {}
    for _, label, p in sorted(days[target]):
        cur = load(p); last = cur; times.append(label)
        for s, row in sym.items():
            c, b = cur.get(s, (None, None)), base[s]
            row["bp"].append(pct(c[0], b[0])); row["sp"].append(pct(c[1], b[1]))
    for s, row in sym.items():
        c = last.get(s, (None, None))
        row.update(bq=c[0], sq=c[1], b0=base[s][0], s0=base[s][1])
    os.makedirs(os.path.join(ROOT, OUT_DIR), exist_ok=True)
    out = {"date": target, "base_date": base_day, "base_time": base_time, "times": times, "sym": sym}
    with open(os.path.join(ROOT, OUT_DIR, target + ".json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("depth saved", target, "| snapshots:", len(times), "| symbols:", len(sym), "| base:", base_day, base_time)

if __name__ == "__main__":
    main()