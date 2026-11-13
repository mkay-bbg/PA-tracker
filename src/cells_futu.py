# %% [markdown]
# # PA TERMINAL (Futu) — market monitor and personal-account positioning
#
# Same terminal as the BQuant version, with **Futu OpenAPI** as the data source. Positions and cash are read live from your Futu / moomoo account.
#
# **How to run**
# 1. Install and start **Futu OpenD** on this machine and log in with your account (default address `127.0.0.1:11111`).
# 2. `pip install futu-api ipywidgets plotly pandas`
# 3. Open this notebook in Jupyter on the same machine and *Run All*.
#
# **What it does with your account:** read-only queries (account list, positions, funds). It never unlocks trading and contains no order calls.
#
# **Differences from the Bloomberg version**
# - Futu has no government yield, swap or spot commodity series, so rates, credit and commodities are tracked through US-listed ETFs, and curve shape through ETF price ratios. There are no curve charts.
# - Each distinct security pulled for history uses one unit of your 30-day historical-candlestick quota. This notebook needs about 40 plus your holdings; the header shows what is left. History is cached per day in `pa_futu_cache_*.pkl`.
# - Securities your quote rights do not cover show as `no data`.
# - There is no economic calendar in the API: list events in `pa_events.csv` (`date,region,event,importance`).
# - Trade dates are not available, so MTD and YTD P&L assume each position was held for the whole period. FX is applied at today's rate only.
#
# If OpenD is not reachable it falls back to **random sample data** and a sample book; the header shows `SAMPLE DATA` in red.
#
# The signal screen is mechanical (z-scores, 1Y ranges, moving averages, your limits). It surfaces things to look at; it is not investment advice.

# %%
# ============================== CONFIG ==============================
OPEND_HOST, OPEND_PORT = '127.0.0.1', 11111
SECURITY_FIRM = 'FUTUSECURITIES'         # FUTUSECURITIES (HK), FUTUINC (moomoo US), FUTUSG, FUTUAU, FUTUCA, FUTUMY, FUTUJP
ACC_ID = 0                               # 0 = first live securities account; otherwise the acc_id to use
BASE_CCY = 'USD'                         # HKD, USD, CNH, JPY, SGD, AUD, CAD, MYR
FX_FALLBACK = {}                         # only used if the account cannot report in a currency, e.g. {'HKD': 0.128} = BASE per 1 HKD
EVENTS_FILE = 'pa_events.csv'            # optional: date,region,event,importance
HISTORY_DAYS = 400                       # covers YTD + 200d average
EVENT_DAYS = 7

# Limits as share of NAV. Edit to your own rules.
LIMITS = {
    'single_name_max': 0.10,
    'fund_max': 0.30,
    'top5_max': 0.65,
    'non_base_ccy_max': 0.40,
    'asset_class_max': {'Equity': 0.70, 'Rates': 0.50, 'Commodity': 0.15, 'Crypto': 0.05},
}
Z_STRETCH = 2.0
DRAWDOWN_FLAG = -0.10

# Holdings are classed as single-name Equity unless listed here: code -> (asset class, single name?)
CLASS_MAP = {
    'US.SPY': ('Equity', False), 'US.VOO': ('Equity', False), 'US.VTI': ('Equity', False), 'US.QQQ': ('Equity', False), 'US.IWM': ('Equity', False),
    'US.VGK': ('Equity', False), 'US.FEZ': ('Equity', False), 'US.EWJ': ('Equity', False), 'US.EEM': ('Equity', False), 'US.VT': ('Equity', False),
    'HK.02800': ('Equity', False), 'HK.03033': ('Equity', False), 'HK.02822': ('Equity', False),
    'US.SHY': ('Rates', False), 'US.IEI': ('Rates', False), 'US.IEF': ('Rates', False), 'US.TLT': ('Rates', False), 'US.TIP': ('Rates', False),
    'US.BND': ('Rates', False), 'US.BNDX': ('Rates', False), 'US.LQD': ('Rates', False), 'US.HYG': ('Rates', False), 'US.SGOV': ('Rates', False), 'US.BIL': ('Rates', False),
    'US.GLD': ('Commodity', False), 'US.IAU': ('Commodity', False), 'US.SLV': ('Commodity', False), 'US.USO': ('Commodity', False),
    'US.IBIT': ('Crypto', False), 'US.FBTC': ('Crypto', False), 'US.ETHA': ('Crypto', False),
}

# (code, label, kind)   kind: p = price (changes in %), v = vol index (changes in pts)
RATES = [
    ('US.SHY', 'UST 1-3Y (SHY)', 'p'), ('US.IEI', 'UST 3-7Y (IEI)', 'p'), ('US.IEF', 'UST 7-10Y (IEF)', 'p'), ('US.TLT', 'UST 20Y+ (TLT)', 'p'),
    ('US.TIP', 'TIPS (TIP)', 'p'), ('US.BNDX', 'Intl bonds hdg (BNDX)', 'p'), ('US.IGOV', 'Intl govt (IGOV)', 'p'),
    ('US.LQD', 'US IG credit (LQD)', 'p'), ('US.HYG', 'US HY credit (HYG)', 'p'), ('US.EMB', 'EM USD debt (EMB)', 'p'),
]
# (label, numerator, denominator): price ratio, rebased to 100 a year ago
RATIOS = [
    ('TLT/SHY long vs short end', 'US.TLT', 'US.SHY'), ('TLT/IEF 20Y+ vs 7-10Y', 'US.TLT', 'US.IEF'),
    ('TIP/IEF inflation vs nominal', 'US.TIP', 'US.IEF'), ('HYG/IEF credit vs govt', 'US.HYG', 'US.IEF'),
    ('IGOV/IEF intl vs US govt', 'US.IGOV', 'US.IEF'), ('QQQ/SPY growth tilt', 'US.QQQ', 'US.SPY'), ('IWM/SPY small vs large', 'US.IWM', 'US.SPY'),
]
EQUITIES = [
    ('US.SPY', 'S&P 500 (SPY)', 'p'), ('US.QQQ', 'Nasdaq 100 (QQQ)', 'p'), ('US.IWM', 'Russell 2000 (IWM)', 'p'),
    ('US.FEZ', 'Euro Stoxx 50 (FEZ)', 'p'), ('US.EWG', 'Germany (EWG)', 'p'), ('US.EWU', 'UK (EWU)', 'p'), ('US.EWJ', 'Japan (EWJ)', 'p'),
    ('HK.800000', 'Hang Seng Index', 'p'), ('HK.800700', 'Hang Seng Tech', 'p'), ('US.EEM', 'MSCI EM (EEM)', 'p'), ('US..VIX', 'VIX', 'v'),
]
COMMOD = [
    ('US.USO', 'WTI (USO)', 'p'), ('US.BNO', 'Brent (BNO)', 'p'), ('US.UNG', 'US nat gas (UNG)', 'p'), ('US.GLD', 'Gold (GLD)', 'p'),
    ('US.SLV', 'Silver (SLV)', 'p'), ('US.CPER', 'Copper (CPER)', 'p'), ('US.DBC', 'Broad commod (DBC)', 'p'),
    ('US.IBIT', 'Bitcoin (IBIT)', 'p'), ('US.ETHA', 'Ether (ETHA)', 'p'),
]
BETA_REF, RATE_REF = 'US.SPY', 'US.IEF'  # book beta and rate-sensitivity references

# Used only when OpenD is not reachable
SAMPLE_POSITIONS = """ticker,name,asset_class,single_name,ccy,qty,avg_cost
US.SPY,S&P 500 ETF,Equity,N,USD,120,520
US.QQQ,Nasdaq 100 ETF,Equity,N,USD,60,450
HK.02800,Tracker Fund of HK,Equity,N,HKD,6000,22
HK.00700,Tencent,Equity,Y,HKD,400,420
US.MSFT,Microsoft,Equity,Y,USD,45,410
US.NVDA,Nvidia,Equity,Y,USD,220,118
US.TLT,20+Y UST ETF,Rates,N,USD,400,90
US.IEF,7-10Y UST ETF,Rates,N,USD,350,94
US.GLD,Gold ETF,Commodity,N,USD,70,240
US.IBIT,Bitcoin ETF,Crypto,N,USD,350,52
CASH,Cash,Cash,N,USD,25000,1
"""

# %%
# ============================== DATA (Futu OpenAPI via OpenD) ==============================
import io, os, zlib, html, time, pickle, socket, datetime as dt
import numpy as np
import pandas as pd

LIVE_TAG = 'FUTU LIVE'
SPREADS, CURVES = [], {}
MONITOR = RATES + EQUITIES + COMMOD
KIND = {t: k for t, _, k in MONITOR}
LABEL = {t: l for t, l, _ in MONITOR}
for _l, _a, _b in RATIOS:
    KIND[_l] = 'p'; LABEL[_l] = _l
SPREAD_KEYS, SPREAD_TITLE, SPREAD_UNIT, RATES_UNIT = [l for l, _, _ in RATIOS], 'Relative value (price ratios)', '1Y ago = 100', 'ETF price / chg %'
BOOK_NOTE = 'Trade dates are not available from the account, so MTD and YTD assume each position was held all period. FX at today\'s rate.'

LIVE, _qc, FUTU_ERR = False, None, ''
try:
    import futu as ft
    socket.create_connection((OPEND_HOST, OPEND_PORT), timeout=2).close()   # the SDK retries forever if OpenD is down, so test first
    _qc = ft.OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    LIVE = True
except Exception as e:
    FUTU_ERR = 'OpenD not reachable at %s:%s (%s)' % (OPEND_HOST, OPEND_PORT, str(e)[:60])


def _weekdays(wide):
    wide = wide.sort_index()
    return wide[wide.index.dayofweek < 5].ffill()


def futu_history(codes):
    """Daily closes (forward-adjusted), wide frame. Cached per day; today's last price overlaid from a snapshot. Returns (wide, errors)."""
    today = pd.Timestamp.today().normalize()
    fn = 'pa_futu_cache_%s.pkl' % today.strftime('%Y%m%d')
    cache = {}
    if os.path.exists(fn):
        with open(fn, 'rb') as f:
            cache = pickle.load(f)
    errs, new = {}, False
    start = (today - pd.Timedelta(days=HISTORY_DAYS)).strftime('%Y-%m-%d')
    for c in codes:
        if c in cache:
            continue
        ret, data, _ = _qc.request_history_kline(c, start=start, end=today.strftime('%Y-%m-%d'), ktype=ft.KLType.K_DAY, autype=ft.AuType.QFQ, max_count=1000)
        if ret == ft.RET_OK and len(data):
            cache[c] = pd.Series(data['close'].astype(float).values, index=pd.to_datetime(data['time_key']).dt.normalize().values)
            new = True
        else:
            errs[c] = str(data)[:80] if ret != ft.RET_OK else 'empty'
        time.sleep(0.55)                 # stay under 60 history requests per 30 seconds
    if new:
        with open(fn, 'wb') as f:
            pickle.dump(cache, f)
    have = [c for c in codes if c in cache]
    wide = pd.DataFrame({c: cache[c][~cache[c].index.duplicated(keep='last')] for c in have})
    for i in range(0, len(have), 300):   # live last price
        try:
            ret, snap = _qc.get_market_snapshot(have[i:i + 300])
            if ret == ft.RET_OK:
                for _, r in snap.iterrows():
                    d = pd.to_datetime(str(r['update_time'])[:10], errors='coerce')
                    if r['last_price'] and r['last_price'] > 0 and pd.notna(d) and d.dayofweek < 5:
                        wide.loc[d, r['code']] = float(r['last_price'])
        except Exception:
            pass
    return _weekdays(wide), errs


def futu_positions():
    """Read-only: positions and cash for the live securities account. Returns (pos frame, fx dict, note)."""
    firm = getattr(ft.SecurityFirm, SECURITY_FIRM)
    tc = ft.OpenSecTradeContext(filter_trdmarket=ft.TrdMarket.NONE, host=OPEND_HOST, port=OPEND_PORT, security_firm=firm)
    try:
        ret, accs = tc.get_acc_list()
        if ret != ft.RET_OK:
            raise RuntimeError('Account list: %s' % accs)
        real = accs[accs['trd_env'].astype(str) == 'REAL']
        if ACC_ID:
            real = real[real['acc_id'] == ACC_ID]
        if real.empty:
            raise RuntimeError('No live account found for %s (accounts seen: %s)' % (SECURITY_FIRM, list(accs['acc_id'])))
        acc = int(real['acc_id'].iloc[0])
        ret, p = tc.position_list_query(trd_env=ft.TrdEnv.REAL, acc_id=acc, refresh_cache=True)
        if ret != ft.RET_OK:
            raise RuntimeError('Positions: %s' % p)

        def total(ccy):
            r, a = tc.accinfo_query(trd_env=ft.TrdEnv.REAL, acc_id=acc, currency=getattr(ft.Currency, ccy))
            if r != ft.RET_OK:
                raise RuntimeError(str(a))
            return float(a['total_assets'].iloc[0]), float(a['cash'].iloc[0])
        tot_b, cash_b = total(BASE_CCY)
        fx = {BASE_CCY: 1.0}
        for ccy in sorted(set(p['currency'].astype(str)) - {BASE_CCY}):
            try:
                tot_c, _ = total(ccy)
                fx[ccy] = tot_b / tot_c          # same account valued in two currencies gives the broker's FX rate
            except Exception:
                if ccy in FX_FALLBACK:
                    fx[ccy] = float(FX_FALLBACK[ccy])
    finally:
        tc.close()
    rows = []
    for _, r in p.iterrows():
        q, px = float(r['qty']), float(r['nominal_price'] or 0)
        if not q:
            continue
        cls, single = CLASS_MAP.get(r['code'], ('Equity', True))
        mult = float(r['market_val']) / (q * px) if px and r['market_val'] else 1.0   # contract multiplier for options / futures
        cost = r['cost_price'] if r.get('cost_price_valid', True) and r['cost_price'] == r['cost_price'] else r['average_cost']
        rows.append(dict(ticker=r['code'], name=r['stock_name'], asset_class=cls, single_name=single, ccy=str(r['currency']), qty=q,
                         avg_cost=float(cost or 0), trade_date=pd.NaT, mult=1.0 if abs(abs(mult) - 1) < 0.02 else abs(mult)))
    rows.append(dict(ticker='CASH', name='Cash (all currencies)', asset_class='Cash', single_name=False, ccy=BASE_CCY, qty=cash_b, avg_cost=1.0,
                     trade_date=pd.NaT, mult=1.0))
    return pd.DataFrame(rows), fx, 'acct %s: broker total assets %s %s' % (str(acc)[-4:].rjust(len(str(acc)), '*'), '{:,.0f}'.format(tot_b), BASE_CCY)


def sample_history(tickers, end_level=None, n=290):
    """Random walks so the notebook runs without OpenD. NOT market data."""
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    out = {}
    for t in tickers:
        rng = np.random.RandomState(zlib.crc32(t.encode()) % (2 ** 31))
        vol = 0.03 if ('IBIT' in t or 'ETHA' in t) else (0.05 if KIND.get(t) == 'v' else 0.011)
        path = np.exp(np.cumsum(rng.normal(0.0003 if KIND.get(t, 'p') == 'p' else 0, vol, n)))
        out[t] = path / path[-1] * (end_level or {}).get(t, 17 if KIND.get(t) == 'v' else 20 + rng.rand() * 400)
    return pd.DataFrame(out, index=idx)


def load_all():
    mon_tk = [t for t, _, _ in MONITOR]
    note, errs = '', {}
    if LIVE:
        pos, fx, note = futu_positions()
        pos_src = 'FUTU ACCOUNT'
        held = [t for t in pos['ticker'] if t != 'CASH']
        allh, errs = futu_history(list(dict.fromkeys(mon_tk + held)))
        mon = allh[[t for t in mon_tk if t in allh]].copy()
        hl = allh[[t for t in held if t in allh]].copy()
        try:
            ret, q = _qc.get_history_kl_quota()
            if ret == ft.RET_OK:
                note += ' | history quota used %s, left %s' % (q[0], q[1])
        except Exception:
            pass
    else:
        pos = pd.read_csv(io.StringIO(SAMPLE_POSITIONS))
        pos['single_name'] = pos['single_name'].astype(str).str.upper().str.startswith('Y')
        pos['trade_date'], pos['mult'], pos_src, fx = pd.NaT, 1.0, 'SAMPLE BOOK', {BASE_CCY: 1.0, 'HKD': 0.128, 'USD': 1.0}
        held = [t for t in pos['ticker'] if t != 'CASH']
        ends = {}
        for _, r in pos.iterrows():
            rng = np.random.RandomState(zlib.crc32(r['ticker'].encode()) % (2 ** 31))
            ends[r['ticker']] = r['avg_cost'] * float(np.exp(rng.normal(0.08, 0.15)))
        mon, hl = sample_history(mon_tk), sample_history(held, ends)
        note = FUTU_ERR
    hb = hl.copy()
    ccy = dict(zip(pos['ticker'], pos['ccy']))
    for t in list(hb.columns):
        if ccy[t] in fx:
            hb[t] = hb[t] * fx[ccy[t]]
        else:
            errs[t] = 'no FX rate for %s: set FX_FALLBACK' % ccy[t]
            hb = hb.drop(columns=t)
    for lab, a, b in RATIOS:
        if a in mon and b in mon:
            r = (mon[a] / mon[b]).dropna()
            if len(r):
                mon[lab] = r / r.iloc[-min(len(r), 260)] * 100
    missing = [t for t in mon_tk if t not in mon or mon[t].dropna().empty] + [t for t in held if t not in hb or hb[t].dropna().empty]
    return dict(mon=mon, hl=hl, hb=hb, pos=pos, pos_src=pos_src, missing=missing, asof=dt.datetime.now(), note=note, errors=errs)


def load_events():
    """Week-ahead events from pa_events.csv (Futu OpenAPI has no economic calendar)."""
    today = pd.Timestamp.today().normalize()
    end = today + pd.Timedelta(days=EVENT_DAYS)
    rows, notes = [], []
    if os.path.exists(EVENTS_FILE):
        ev = pd.read_csv(EVENTS_FILE)
        ev['date'] = pd.to_datetime(ev['date'])
        for _, r in ev[(ev['date'] >= today) & (ev['date'] <= end)].iterrows():
            rows.append((r['date'], r.get('region', ''), r['event'], str(r.get('importance', '')).upper(), 'USR'))
    else:
        notes.append('No %s found. Futu has no economic calendar, so add your own events file (date,region,event,importance).' % EVENTS_FILE)
    cols = ['date', 'region', 'event', 'importance', 'src']
    return (pd.DataFrame(rows, columns=cols).sort_values('date') if rows else pd.DataFrame(columns=cols)), notes
