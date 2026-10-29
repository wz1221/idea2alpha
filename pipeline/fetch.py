"""增量抓取 FinMind 資料到 data/raw/*.parquet。環境變數：FINMIND_TOKEN"""
import os, time, requests, pandas as pd
from pathlib import Path
API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = os.environ.get("START", "2019-01-01")
RAW = Path("data/raw"); RAW.mkdir(parents=True, exist_ok=True)
DS = {"price": "TaiwanStockPrice", "per": "TaiwanStockPER",
      "inst": "TaiwanStockInstitutionalInvestorsBuySell",
      "margin": "TaiwanStockMarginPurchaseShortSale", "rev": "TaiwanStockMonthRevenue"}

def get(ds, sid, start):
    for i in range(3):
        r = requests.get(API, params={"dataset": ds, "data_id": sid, "start_date": start, "token": TOKEN}, timeout=60)
        if r.status_code == 200 and r.json().get("status") == 200:
            return pd.DataFrame(r.json()["data"])
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"fetch failed: {ds} {sid}")

def universe():
    return [l.strip() for l in open("strategies/universe.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]

def main():
    for sid in universe():
        for k, ds in DS.items():
            f = RAW / f"{k}_{sid}.parquet"
            old = pd.read_parquet(f) if f.exists() else None
            start = START if old is None or old.empty else str(old["date"].max())
            new = get(ds, sid, start)
            df = pd.concat([old, new]).drop_duplicates() if old is not None else new
            df.to_parquet(f); time.sleep(0.3)
    info = get("TaiwanStockInfo", "", "2019-01-01")[["stock_id", "stock_name"]].drop_duplicates("stock_id")
    info.to_parquet(RAW / "info.parquet")

if __name__ == "__main__":
    main()
