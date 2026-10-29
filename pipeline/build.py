"""由 raw parquet 算出 10 個因子，輸出前端用的 latest.json。
資料時點：月營收於次月 10 日後才可用；因子只用當日以前資料。ROE 以 PBR/PER 近似。"""
import json, os, numpy as np, pandas as pd
from pathlib import Path
RAW = Path("data/raw"); OUT = Path(os.environ.get("OUT", "web/public/data/latest.json"))
TOPN = int(os.environ.get("TOPN", "300"))
def rd(k, sid):
    f = RAW / f"{k}_{sid}.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()

def feats(sid):
    p = rd("price", sid)
    if len(p) < 300: return None
    p["date"] = pd.to_datetime(p["date"]); p = p.drop_duplicates("date").set_index("date").sort_index()
    c = p["close"].astype(float); o = pd.DataFrame({"close": c}); r = c.pct_change()
    o["var5q"] = r.rolling(250).quantile(.05); o["mom"] = c.shift(21) / c.shift(252) - 1
    ma = lambda n: c.rolling(n).mean()
    o["ma"] = ((ma(5) > ma(10)) & (ma(10) > ma(20)) & (ma(20) > ma(60)) & (c > ma(5))).astype(float)
    hi = c.rolling(252).max(); o["nh"] = (hi - c) / hi * 100
    o["turn"] = p["Trading_money"].astype(float).rolling(20).mean()
    per = rd("per", sid)
    if len(per):
        per["date"] = pd.to_datetime(per["date"]); per = per.drop_duplicates("date").set_index("date").sort_index()
        pe = per["PER"].replace(0, np.nan)
        for k, s in (("pe", pe), ("yld", per["dividend_yield"]), ("roe", per["PBR"] / pe * 100)):
            o[k] = s.reindex(o.index, method="ffill")
    ins = rd("inst", sid)
    if len(ins):
        ins = ins[ins["name"].isin(["Foreign_Investor", "Investment_Trust"])].copy()
        ins["date"] = pd.to_datetime(ins["date"]); ins["net"] = ins["buy"] - ins["sell"]
        n = ins.groupby("date")["net"].sum().reindex(o.index).fillna(0)
        o["inst"] = (n > 0).groupby((n <= 0).cumsum()).cumsum()
    mg = rd("margin", sid)
    if len(mg):
        mg["date"] = pd.to_datetime(mg["date"])
        mb = mg.drop_duplicates("date").set_index("date").sort_index()["MarginPurchaseTodayBalance"]
        o["mg"] = mb.reindex(o.index, method="ffill").pct_change(10) * 100
    rv = rd("rev", sid)
    if len(rv):
        rv["ym"] = rv["revenue_year"] * 12 + rv["revenue_month"] - 1
        s = rv.drop_duplicates("ym").set_index("ym")["revenue"].sort_index()
        s = s.reindex(range(int(s.index.min()), int(s.index.max()) + 1)); s3 = s.rolling(3).sum(); yoy = s3 / s3.shift(12) - 1
        avail = pd.to_datetime([f"{i // 12}-{i % 12 + 1:02d}-01" for i in yoy.index]) + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9)
        a = pd.Series(yoy.values * 100, index=avail); a = a[~a.index.duplicated()]
        o["rev"] = a.reindex(o.index, method="ffill")
    for k in ("pe", "yld", "roe", "inst", "mg", "rev"):
        if k not in o: o[k] = np.nan
    return o

NEED = ["var5q", "mom", "nh", "rev", "roe"]
COLS = ["rev", "inst", "var5", "pe", "roe", "mom", "ma", "mg", "nh", "yld"]
def cross(rows):
    d = pd.DataFrame(rows).T.dropna(subset=NEED).copy()
    d["pe"] = d["pe"].fillna(99); d["yld"] = d["yld"].fillna(0); d["mg"] = d["mg"].fillna(0); d["inst"] = d["inst"].fillna(0)
    d = d.sort_values("turn", ascending=False).head(TOPN)
    d["var5"] = (-d["var5q"]).rank(pct=True); d["mom"] = d["mom"].rank(pct=True)
    return d

def main():
    ids = [l.strip() for l in open("strategies/universe.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]
    fr = {s: f for s in ids if (f := feats(s)) is not None}
    names = pd.read_parquet(RAW / "info.parquet").set_index("stock_id")["stock_name"].to_dict()
    mo = {s: f.groupby(f.index.to_period("M")).tail(1).assign(fwd=lambda x: x["close"].shift(-1) / x["close"] - 1) for s, f in fr.items()}
    dates = sorted(set().union(*[set(m.index) for m in mo.values()]))[:-1]
    periods = []
    for d in dates:
        rows = {s: m.loc[d] for s, m in mo.items() if d in m.index and not np.isnan(m.loc[d, "fwd"])}
        if len(rows) < 10: continue
        t = cross(rows)
        if len(t) >= 10:
            periods.append([{**{k: round(float(r[k]), 3) for k in COLS}, "fwd": round(float(r["fwd"]), 4)} for _, r in t.iterrows()])
    last = max(f.index.max() for f in fr.values())
    now = cross({s: f.iloc[-1] for s, f in fr.items() if (last - f.index[-1]).days <= 5})
    out = {"asof": str(last.date()), "periods": periods,
           "now": [{"name": f"{s} {names.get(s, '')}", **{k: round(float(r[k]), 3) for k in COLS}} for s, r in now.iterrows()]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("periods", len(periods), "now", len(now))

if __name__ == "__main__":
    main()
