"""
OCC backend (Analytics Token wala): Upstox se option chain leta hai, 75 strikes deta hai.
Roz login ki zaroorat nahi. Sirf ek environment variable chahiye: UPSTOX_TOKEN
"""
import gzip, io, json, os, time
from datetime import date

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

BASE = "https://api.upstox.com/v2"
CACHE_SECONDS = 60
HALF = 37  # 37 + ATM + 37 = 75 strikes

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
cache = {}
inst_keys = {}


def headers():
    t = os.environ.get("UPSTOX_TOKEN")
    if not t:
        raise HTTPException(500, "UPSTOX_TOKEN set nahi hai.")
    return {"Authorization": f"Bearer {t}", "Accept": "application/json"}


@app.get("/")
def home():
    return {"ok": True, "msg": "OCC backend chal raha hai"}


def load_instruments():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    raw = requests.get(url, timeout=60).content
    for row in json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)))):
        if row.get("segment") == "NSE_EQ" and row.get("instrument_type") == "EQ":
            inst_keys[row["trading_symbol"]] = row["instrument_key"]


def nearest_expiry(ikey):
    r = requests.get(f"{BASE}/option/contract", headers=headers(),
                     params={"instrument_key": ikey}, timeout=20)
    r.raise_for_status()
    days = sorted({c["expiry"] for c in r.json().get("data", [])
                   if c["expiry"] >= str(date.today())})
    if not days:
        raise HTTPException(404, "Is stock mein option nahi hain.")
    return days[0]


def fetch_chain(symbol):
    if not inst_keys:
        load_instruments()
    ikey = inst_keys.get(symbol)
    if not ikey:
        raise HTTPException(404, f"{symbol} nahi mila.")
    r = requests.get(f"{BASE}/option/chain", headers=headers(), timeout=20,
                     params={"instrument_key": ikey,
                             "expiry_date": nearest_expiry(ikey)})
    r.raise_for_status()
    rows = r.json().get("data", [])
    if not rows:
        raise HTTPException(404, "Data khali aaya.")
    spot = rows[0].get("underlying_spot_price", 0)
    rows.sort(key=lambda x: x["strike_price"])
    atm = min(range(len(rows)), key=lambda i: abs(rows[i]["strike_price"] - spot))
    rows = rows[max(0, atm - HALF): atm + HALF + 1]
    out = []
    for x in rows:
        c = x["call_options"]["market_data"]
        p = x["put_options"]["market_data"]
        out.append({
            "strike": x["strike_price"],
            "ceOI": int(c.get("oi", 0)), "ceChg": int(c.get("oi", 0) - c.get("prev_oi", 0)),
            "ceVol": int(c.get("volume", 0)),
            "peOI": int(p.get("oi", 0)), "peChg": int(p.get("oi", 0) - p.get("prev_oi", 0)),
            "peVol": int(p.get("volume", 0))})
    return {"spot": spot, "records": out}


@app.get("/api/chain/{symbol}")
def chain(symbol: str):
    symbol = symbol.upper()
    hit = cache.get(symbol)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = fetch_chain(symbol)
    cache[symbol] = (time.time(), data)
    return data
