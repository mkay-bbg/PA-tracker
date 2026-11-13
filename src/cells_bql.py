# %% [markdown]
# # PA TERMINAL — market monitor and personal-account positioning
#
# Runs in **Bloomberg BQuant** (BQNT <GO>). Data comes from BQL; the UI is ipywidgets + plotly.
#
# **How to use**
# 1. Upload this notebook to a BQuant project and *Run All*.
# 2. Replace the sample book: upload a `pa_positions.csv` next to the notebook (same columns as the sample in the config cell), or edit `SAMPLE_POSITIONS` directly.
# 3. Optional: upload `pa_events.csv` (`date,region,event,importance`) for events you want on the week-ahead calendar (central bank meetings, auctions, etc.).
# 4. Press **REFRESH** to re-pull; **EXPORT WEEKLY BRIEF** writes a standalone HTML brief to the project folder.
#
# Outside BQuant (no `bql`), it falls back to **random sample data** so the layout can be checked; the header shows `SAMPLE DATA` in red.
#
# The signal screen is mechanical (z-scores, 1Y ranges, moving averages, your limits). It surfaces things to look at; it is not investment advice.

# %%
# ============================== CONFIG ==============================
BASE_CCY = 'USD'
POSITIONS_FILE = 'pa_positions.csv'      # if present, used instead of SAMPLE_POSITIONS
EVENTS_FILE = 'pa_events.csv'            # optional: date,region,event,importance
HISTORY = '-400D'                        # BQL lookback (covers YTD + 200d MA)
EVENT_DAYS = 7                           # week-ahead horizon

# Limits as share of NAV. Edit to your own rules.
LIMITS = {
    'single_name_max': 0.10,             # any one single-name position
    'fund_max': 0.30,                    # any one fund / ETF
    'top5_max': 0.65,                    # five largest positions together
    'non_base_ccy_max': 0.40,            # exposure outside BASE_CCY
    'asset_class_max': {'Equity': 0.70, 'Rates': 0.50, 'Commodity': 0.15, 'Crypto': 0.05},
}
Z_STRETCH = 2.0                          # |z| of 5D move that counts as stretched
DRAWDOWN_FLAG = -0.10                    # position drawdown from 3M high that gets flagged

# qty * avg_cost are in the security's quote currency (pence for GBp lines). Cash: ticker CASH, qty = amount in BASE_CCY.
SAMPLE_POSITIONS = """ticker,name,asset_class,single_name,ccy,qty,avg_cost,trade_date
SPY US Equity,S&P 500 ETF,Equity,N,USD,120,520,2025-03-14
QQQ US Equity,Nasdaq 100 ETF,Equity,N,USD,60,450,2025-06-02
VGK US Equity,Europe ETF,Equity,N,USD,300,68,2025-01-21
SX5EEX GY Equity,Euro Stoxx 50 ETF,Equity,N,EUR,400,48,2026-02-10
MSFT US Equity,Microsoft,Equity,Y,USD,45,410,2024-11-05
NVDA US Equity,Nvidia,Equity,Y,USD,220,118,2025-04-22
TLT US Equity,20+Y UST ETF,Rates,N,USD,400,90,2026-05-18
IEF US Equity,7-10Y UST ETF,Rates,N,USD,350,94,2025-09-08
GLD US Equity,Gold ETF,Commodity,N,USD,70,240,2025-02-03
IBIT US Equity,Bitcoin ETF,Crypto,N,USD,350,52,2026-10-20
CASH,Cash,Cash,N,USD,25000,1,2026-01-02
"""

# (ticker, label, kind)   kind: y = yield (%; changes in bp), p = price (changes in %), v = vol index (changes in pts)
RATES = [
    ('USGG2YR Index', 'UST 2Y', 'y'), ('USGG5YR Index', 'UST 5Y', 'y'), ('USGG10YR Index', 'UST 10Y', 'y'), ('USGG30YR Index', 'UST 30Y', 'y'),
    ('GDBR2 Index', 'Bund 2Y', 'y'), ('GDBR5 Index', 'Bund 5Y', 'y'), ('GDBR10 Index', 'Bund 10Y', 'y'), ('GDBR30 Index', 'Bund 30Y', 'y'),
    ('GUKG2 Index', 'Gilt 2Y', 'y'), ('GUKG5 Index', 'Gilt 5Y', 'y'), ('GUKG10 Index', 'Gilt 10Y', 'y'), ('GUKG30 Index', 'Gilt 30Y', 'y'),
    ('GJGB2 Index', 'JGB 2Y', 'y'), ('GJGB5 Index', 'JGB 5Y', 'y'), ('GJGB10 Index', 'JGB 10Y', 'y'), ('GJGB30 Index', 'JGB 30Y', 'y'),
    ('GFRN10 Index', 'OAT 10Y', 'y'), ('GBTPGR10 Index', 'BTP 10Y', 'y'), ('GACGB10 Index', 'ACGB 10Y', 'y'), ('GCAN10YR Index', 'CAN 10Y', 'y'),
    ('USOSFR10 Curncy', 'SOFR swap 10Y', 'y'), ('EUSA10 Curncy', 'EUR swap 10Y', 'y'), ('MOVE Index', 'MOVE', 'v'),
]
# (label, long leg, short leg): level = (long - short) in bp
SPREADS = [
    ('US 2s10s', 'USGG10YR Index', 'USGG2YR Index'), ('US 5s30s', 'USGG30YR Index', 'USGG5YR Index'),
    ('DE 2s10s', 'GDBR10 Index', 'GDBR2 Index'), ('UK 2s10s', 'GUKG10 Index', 'GUKG2 Index'), ('JP 2s10s', 'GJGB10 Index', 'GJGB2 Index'),
    ('UST-Bund 10Y', 'USGG10YR Index', 'GDBR10 Index'), ('Gilt-Bund 10Y', 'GUKG10 Index', 'GDBR10 Index'),
    ('BTP-Bund 10Y', 'GBTPGR10 Index', 'GDBR10 Index'), ('OAT-Bund 10Y', 'GFRN10 Index', 'GDBR10 Index'),
    ('US 10Y swap spd', 'USOSFR10 Curncy', 'USGG10YR Index'),
]
EQUITIES = [
    ('SPX Index', 'S&P 500', 'p'), ('NDX Index', 'Nasdaq 100', 'p'), ('RTY Index', 'Russell 2000', 'p'),
    ('SX5E Index', 'Euro Stoxx 50', 'p'), ('DAX Index', 'DAX', 'p'), ('UKX Index', 'FTSE 100', 'p'),
    ('NKY Index', 'Nikkei 225', 'p'), ('HSI Index', 'Hang Seng', 'p'), ('MXEF Index', 'MSCI EM', 'p'), ('VIX Index', 'VIX', 'v'),
]
COMMOD = [
    ('CL1 Comdty', 'WTI', 'p'), ('CO1 Comdty', 'Brent', 'p'), ('NG1 Comdty', 'US nat gas', 'p'),
    ('XAU Curncy', 'Gold', 'p'), ('XAG Curncy', 'Silver', 'p'), ('HG1 Comdty', 'Copper', 'p'), ('BCOM Index', 'BCOM', 'p'),
    ('XBTUSD Curncy', 'Bitcoin', 'p'), ('XETUSD Curncy', 'Ether', 'p'),
]
CURVES = {
    'US': [(2, 'USGG2YR Index'), (5, 'USGG5YR Index'), (10, 'USGG10YR Index'), (30, 'USGG30YR Index')],
    'DE': [(2, 'GDBR2 Index'), (5, 'GDBR5 Index'), (10, 'GDBR10 Index'), (30, 'GDBR30 Index')],
    'UK': [(2, 'GUKG2 Index'), (5, 'GUKG5 Index'), (10, 'GUKG10 Index'), (30, 'GUKG30 Index')],
    'JP': [(2, 'GJGB2 Index'), (5, 'GJGB5 Index'), (10, 'GJGB10 Index'), (30, 'GJGB30 Index')],
}
# Economic releases to look for on the week-ahead calendar (BQL lookup is best-effort; see load_events)
ECO_TICKERS = {
    'NFP TCH Index': ('US', 'Nonfarm payrolls'), 'CPI YOY Index': ('US', 'CPI y/y'), 'CPI XYOY Index': ('US', 'Core CPI y/y'),
    'PCE CYOY Index': ('US', 'Core PCE y/y'), 'NAPMPMI Index': ('US', 'ISM manufacturing'), 'INJCJC Index': ('US', 'Initial jobless claims'),
    'RSTAMOM Index': ('US', 'Retail sales m/m'), 'GDP CQOQ Index': ('US', 'GDP q/q saar'), 'FDTR Index': ('US', 'FOMC decision'),
    'ECCPEMUY Index': ('EZ', 'HICP y/y'), 'EUORDEPO Index': ('EZ', 'ECB decision'), 'UKRPCJYR Index': ('UK', 'CPI y/y'),
    'UKBRBASE Index': ('UK', 'BoE decision'), 'JNCPIYOY Index': ('JP', 'CPI y/y'), 'BOJDTR Index': ('JP', 'BoJ decision'),
}

# %%
# ============================== DATA ==============================
import io, os, zlib, html, datetime as dt
import numpy as np
import pandas as pd

try:
    import bql
    bq = bql.Service()
    LIVE = True
except Exception:
    bq = None
    LIVE = False

MONITOR = RATES + EQUITIES + COMMOD
BETA_REF, RATE_REF, LIVE_TAG = 'SPX Index', 'USGG10YR Index', 'BQL LIVE'
SPREAD_KEYS, SPREAD_TITLE, SPREAD_UNIT, RATES_UNIT = [l for l, _, _ in SPREADS], 'Curves and spreads', 'bp', 'Yield % / chg bp'
BOOK_NOTE = 'Positions opened inside a period are measured from average cost.'
KIND = {t: k for t, _, k in MONITOR}
LABEL = {t: l for t, l, _ in MONITOR}
for _l, _a, _b in SPREADS:
    KIND[_l] = 's'; LABEL[_l] = _l


def _weekdays(wide):
    wide = wide.sort_index()
    wide = wide[wide.index.dayofweek < 5].ffill()
    return wide


def bql_history(tickers, currency=None):
    """Daily PX_LAST history, wide frame (dates x tickers)."""
    kw = dict(dates=bq.func.range(HISTORY, '0D'), fill='prev')
    if currency:
        kw['currency'] = currency
    res = bq.execute(bql.Request(list(tickers), {'px': bq.data.px_last(**kw)}))
    df = res[0].df().reset_index()
    df['DATE'] = pd.to_datetime(df['DATE'])
    wide = df.pivot_table(index='DATE', columns='ID', values='px', aggfunc='last')
    return _weekdays(wide)


_ANCHOR = {'USGG2YR Index': 3.9, 'USGG5YR Index': 4.0, 'USGG10YR Index': 4.3, 'USGG30YR Index': 4.8, 'GDBR2 Index': 2.0, 'GDBR5 Index': 2.3,
           'GDBR10 Index': 2.6, 'GDBR30 Index': 3.0, 'GUKG2 Index': 3.9, 'GUKG5 Index': 4.1, 'GUKG10 Index': 4.5, 'GUKG30 Index': 5.2,
           'GJGB2 Index': 0.9, 'GJGB5 Index': 1.2, 'GJGB10 Index': 1.6, 'GJGB30 Index': 3.0, 'GFRN10 Index': 3.4, 'GBTPGR10 Index': 3.5,
           'GACGB10 Index': 4.3, 'GCAN10YR Index': 3.2, 'USOSFR10 Curncy': 3.8, 'EUSA10 Curncy': 2.6, 'MOVE Index': 90, 'VIX Index': 17,
           'SPX Index': 6000, 'NDX Index': 21000, 'RTY Index': 2300, 'SX5E Index': 5300, 'DAX Index': 22000, 'UKX Index': 8800, 'NKY Index': 40000,
           'HSI Index': 23000, 'MXEF Index': 1200, 'CL1 Comdty': 65, 'CO1 Comdty': 69, 'NG1 Comdty': 3.5, 'XAU Curncy': 3300, 'XAG Curncy': 38,
           'HG1 Comdty': 4.6, 'BCOM Index': 103, 'XBTUSD Curncy': 100000, 'XETUSD Curncy': 3500}


def sample_history(tickers, end_level=None, n=290):
    """Random walks so the notebook runs without Bloomberg. NOT market data."""
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    out = {}
    for t in tickers:
        rng = np.random.RandomState(zlib.crc32(t.encode()) % (2 ** 31))
        k = KIND.get(t, 'p')
        if k == 'y':
            path = np.cumsum(rng.normal(0, 0.045, n))
            path = path - path[-1] + _ANCHOR.get(t, 3.0)
        else:
            vol = 0.03 if ('XBT' in t or 'XET' in t or 'IBIT' in t) else (0.05 if k == 'v' else 0.011)
            path = np.exp(np.cumsum(rng.normal(0.0003 if k == 'p' else 0, vol, n)))
            tgt = (end_level or {}).get(t, _ANCHOR.get(t, 100 * (1 + rng.rand() * 50)))
            path = path / path[-1] * tgt
        out[t] = path
    return pd.DataFrame(out, index=idx)


def load_positions():
    if os.path.exists(POSITIONS_FILE):
        pos, src = pd.read_csv(POSITIONS_FILE), POSITIONS_FILE
    else:
        pos, src = pd.read_csv(io.StringIO(SAMPLE_POSITIONS)), 'SAMPLE BOOK'
    pos['trade_date'] = pd.to_datetime(pos['trade_date'])
    pos['single_name'] = pos['single_name'].astype(str).str.upper().str.startswith('Y')
    pos['ticker'] = pos['ticker'].astype(str).str.strip()
    return pos, src


def load_all():
    """Returns dict with monitor history, position history (local + base ccy), positions, flags."""
    pos, pos_src = load_positions()
    held = [t for t in pos['ticker'] if t.upper() != 'CASH']
    mon_tk = [t for t, _, _ in MONITOR]
    if LIVE:
        mon = bql_history(mon_tk)
        hl = bql_history(held) if held else pd.DataFrame()
        hb = bql_history(held, currency=BASE_CCY) if held else pd.DataFrame()
    else:
        mon = sample_history(mon_tk)
        ends = {}
        for _, r in pos.iterrows():
            rng = np.random.RandomState(zlib.crc32(r['ticker'].encode()) % (2 ** 31))
            ends[r['ticker']] = r['avg_cost'] * float(np.exp(rng.normal(0.08, 0.15)))
        hl = sample_history(held, ends)
        hb = hl.copy()
        for _, r in pos.iterrows():
            if r['ticker'] in hb and r['ccy'] != BASE_CCY:
                rng = np.random.RandomState(7)
                hb[r['ticker']] = hb[r['ticker']] * (1.08 * np.exp(np.cumsum(rng.normal(0, 0.004, len(hb)))))
    for lab, a, b in SPREADS:
        if a in mon and b in mon:
            mon[lab] = (mon[a] - mon[b]) * 100
    missing = [t for t in mon_tk if t not in mon or mon[t].dropna().empty] + [t for t in held if t not in hb or hb[t].dropna().empty]
    return dict(mon=mon, hl=hl, hb=hb, pos=pos, pos_src=pos_src, missing=missing, asof=dt.datetime.now())


def load_events():
    """Week-ahead events: BQL eco calendar + earnings for held names (both best-effort), plus pa_events.csv."""
    today = pd.Timestamp.today().normalize()
    end = today + pd.Timedelta(days=EVENT_DAYS)
    rows, notes = [], []

    def _dt(v):
        try:
            if isinstance(v, (int, float, np.integer, np.floating)) and not pd.isna(v):
                return pd.to_datetime(str(int(v)), format='%Y%m%d')
            return pd.to_datetime(v)
        except Exception:
            return pd.NaT

    if LIVE:
        try:
            df = bq.execute(bql.Request(list(ECO_TICKERS), {'d': bq.data.eco_release_dt()}))[0].df()
            for tk, r in df.iterrows():
                d = _dt(r['d'])
                if pd.notna(d) and today <= d.normalize() <= end:
                    reg, name = ECO_TICKERS.get(tk, ('', tk))
                    rows.append((d.normalize(), reg, name, 'HIGH', 'ECO'))
        except Exception as e:
            notes.append('Economic calendar not loaded from BQL (%s). Check the release-date field in BQL Builder, or list events in %s.' % (str(e)[:80], EVENTS_FILE))
        try:
            pos, _ = load_positions()
            names = list(pos.loc[pos['single_name'], 'ticker'])
            if names:
                df = bq.execute(bql.Request(names, {'d': bq.data.expected_report_dt()}))[0].df()
                for tk, r in df.iterrows():
                    d = _dt(r['d'])
                    if pd.notna(d) and today <= d.normalize() <= end:
                        rows.append((d.normalize(), 'BOOK', '%s earnings' % tk, 'HIGH', 'ERN'))
        except Exception as e:
            notes.append('Earnings dates not loaded from BQL (%s).' % str(e)[:80])
    else:
        bd = pd.bdate_range(today, periods=6)
        for i, (reg, name) in enumerate([('US', 'CPI y/y (sample)'), ('UK', 'BoE decision (sample)'), ('US', 'Retail sales m/m (sample)'),
                                         ('EZ', 'HICP y/y (sample)'), ('BOOK', 'NVDA US Equity earnings (sample)')]):
            rows.append((bd[i + 1], reg, name, 'HIGH', 'SMP'))
        notes.append('Sample events with made-up dates. In BQuant these come from BQL and %s.' % EVENTS_FILE)
    if os.path.exists(EVENTS_FILE):
        ev = pd.read_csv(EVENTS_FILE)
        ev['date'] = pd.to_datetime(ev['date'])
        for _, r in ev[(ev['date'] >= today) & (ev['date'] <= end)].iterrows():
            rows.append((r['date'], r.get('region', ''), r['event'], str(r.get('importance', '')).upper(), 'USR'))
    ev = pd.DataFrame(rows, columns=['date', 'region', 'event', 'importance', 'src']).sort_values('date') if rows else \
        pd.DataFrame(columns=['date', 'region', 'event', 'importance', 'src'])
    return ev, notes

