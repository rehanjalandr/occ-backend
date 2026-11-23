"""
OCC backend: Upstox se option chain leta hai, 75 strikes deta hai.
Chalane ke liye:  uvicorn main:app --host 0.0.0.0 --port 8000
Roz subah ek baar browser mein  /login  kholo (token roz expire hota hai).
"""
import gzip, io, json, os, time
from datetime import date

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

KEY = os.environ["UPSTOX_KEY"]            # API key
SECRET = os.environ["UPSTOX_SECRET"]      # API secret (sirf yahin rakho)
REDIRECT = os.environ["UPSTOX_REDIRECT"]  # jaise https://tumhara-server.com/callback
BASE = "https://api.upstox.com/v2"
TOKEN_FILE = "token.json"
CACHE_SECONDS = 60
HALF = 37                                 # 37 + ATM + 37 = 75 strikes

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
cache = {}            # symbol -> (time, data)
inst_keys = {}        # symbol -> instrument_key


def load_token():
    try:
        d = json.load(open(TOKEN_FILE))
        return d["token"] if d["day"] == str(date.today()) else None
    except Exception:
        return None


def headers():
    t = load_token()
    if not t:
        raise HTTPException(401, "Token nahi hai. Pehle /login kholo.")
    return {"Authorization": f"Bearer {t}", "Accept": "application/json"}


@app.get("/login")
def login():
    return RedirectResponse(
        f"{BASE}/login/authorization/dialog?response_type=code"
        f"&client_id={KEY}&redirect_uri={REDIRECT}")


@app.get("/callback")
def callback(code: str):
    r = requests.post(f"{BASE}/login/authorization/token", data={
        "code": code, "client_id": KEY, "client_secret": SECRET,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"},
        headers={"Accept": "application/json"}, timeout=20)
    if r.status_code != 200:
        raise HTTPException(400, r.text)
    json.dump({"day": str(date.today()), "token": r.json()["access_token"]},
              open(TOKEN_FILE, "w"))
    return {"ok": True, "msg": "Token mil gaya, aaj ke liye kaafi hai."}


def load_instruments():
    """NSE ki instrument list se symbol -> instrument_key map banata hai."""
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
