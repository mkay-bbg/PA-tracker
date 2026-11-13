# %%
# ============================== ANALYTICS ==============================
def _chg(s, n, kind):
    s = s.dropna()
    if len(s) <= n:
        return np.nan
    a, b = s.iloc[-1], s.iloc[-1 - n]
    if kind == 'p':
        return (a / b - 1) * 100
    return (a - b) * (100 if kind == 'y' else 1)


def _ref_before(s, date):
    s = s.dropna()
    s = s[s.index < date]
    return s.iloc[-1] if len(s) else np.nan


def stats(s, kind):
    """Level, changes (bp for y/s, % for p, pts for v), 1Y range, z-score of the latest 5D move."""
    s = s.dropna()
    if s.empty:
        return None
    last = s.iloc[-1]
    y0 = _ref_before(s, pd.Timestamp(s.index[-1].year, 1, 1))
    if kind == 'p':
        ytd = (last / y0 - 1) * 100 if y0 == y0 else np.nan
        mv5 = s.pct_change(5) * 100
    else:
        ytd = (last - y0) * (100 if kind == 'y' else 1) if y0 == y0 else np.nan
        mv5 = s.diff(5) * (100 if kind == 'y' else 1)
    mv5 = mv5.dropna()
    z5 = (mv5.iloc[-1] - mv5.mean()) / mv5.std() if len(mv5) > 30 and mv5.std() > 0 else np.nan
    yr = s.iloc[-260:]
    lo, hi = yr.min(), yr.max()
    ma50 = s.rolling(50).mean()
    cross = 0
    if kind == 'p' and len(s) > 56:
        side = np.sign(s - ma50).iloc[-6:]
        if side.iloc[-1] != side.iloc[0] and side.iloc[0] != 0:
            cross = int(side.iloc[-1])
    return dict(last=last, d1=_chg(s, 1, kind), d5=_chg(s, 5, kind), d21=_chg(s, 21, kind), ytd=ytd, lo=lo, hi=hi,
                pctile=(last - lo) / (hi - lo) if hi > lo else 0.5, z5=z5, cross=cross, s=s)


def monitor_stats(mon):
    out = {}
    for t in mon.columns:
        st = stats(mon[t], KIND.get(t, 'p'))
        if st:
            out[t] = st
    return out


def book(D):
    """Position-level P&L and exposure in BASE_CCY, plus back-cast NAV of the current holdings."""
    pos, hl, hb = D['pos'], D['hl'], D['hb']
    last_dt = hb.index[-1] if len(hb) else pd.Timestamp.today().normalize()
    m0, y0 = pd.Timestamp(last_dt.year, last_dt.month, 1), pd.Timestamp(last_dt.year, 1, 1)
    rows, nav_hist = [], pd.Series(0.0, index=hb.index if len(hb) else [last_dt])
    for _, r in pos.iterrows():
        tk, q = r['ticker'], float(r['qty']) * float(r.get('mult', 1) or 1)
        if tk.upper() == 'CASH':
            rows.append(dict(ticker='CASH', name=r['name'], asset_class='Cash', single=False, ccy=r['ccy'], qty=q, px=1.0, d1p=0.0,
                             mv=q, cost=q, upl=0.0, pd1=0.0, pmtd=0.0, pytd=0.0, dd=0.0, z5=np.nan))
            nav_hist = nav_hist + q
            continue
        if tk not in hb or hb[tk].dropna().empty:
            continue
        sb, sl = hb[tk].dropna(), hl[tk].dropna()
        pxb, pxl = sb.iloc[-1], sl.iloc[-1]
        fx = pxb / pxl
        cost_b = float(r['avg_cost']) * fx

        def pnl(ref_date):
            ref = _ref_before(sb, ref_date)
            if r['trade_date'] >= ref_date or ref != ref:   # opened inside the period: measure from cost
                ref = cost_b
            return q * (pxb - ref)
        st = stats(sb, 'p')
        hi63 = sb.iloc[-63:].max()
        rows.append(dict(ticker=tk, name=r['name'], asset_class=r['asset_class'], single=bool(r['single_name']), ccy=r['ccy'], qty=float(r['qty']),
                         px=pxl, d1p=st['d1'], mv=q * pxb, cost=q * cost_b, upl=q * (pxb - cost_b), pd1=pnl(sb.index[-1]),
                         pmtd=pnl(m0), pytd=pnl(y0), dd=pxb / hi63 - 1, z5=st['z5']))
        nav_hist = nav_hist + q * hb[tk].ffill().bfill()
    b = pd.DataFrame(rows)
    nav = b['mv'].sum()
    b['w'] = b['mv'] / nav
    b = b.sort_values('mv', ascending=False).reset_index(drop=True)
    return b, nav, nav_hist


def risk(b, nav, nav_hist, mon):
    r = nav_hist.pct_change().dropna().iloc[-252:]
    out = dict(vol=r.std() * np.sqrt(252) if len(r) > 20 else np.nan,
               var95=-np.percentile(r, 5) * nav if len(r) > 20 else np.nan, beta=np.nan, bp10=np.nan)
    if BETA_REF in mon and len(r) > 20:
        x = mon[BETA_REF].pct_change().reindex(r.index).dropna()
        yv = r.reindex(x.index)
        if x.var() > 0:
            out['beta'] = float(np.cov(yv, x)[0, 1] / x.var())
    if RATE_REF in mon and len(r) > 20:
        isy = KIND.get(RATE_REF) == 'y'
        x = (mon[RATE_REF].diff() * 100 if isy else mon[RATE_REF].pct_change() * 100).reindex(r.index).dropna()
        yv = r.reindex(x.index)
        if x.var() > 0:
            out['bp10'] = float(np.cov(yv, x)[0, 1] / x.var()) * nav   # empirical P&L per +1bp in a yield, or per +1% in a bond-fund proxy
    return out


def limit_checks(b):
    """[(limit name, current, limit, status)]"""
    out = []

    def add(name, val, lim):
        out.append((name, val, lim, 'BREACH' if val > lim else ('WARN' if val > 0.85 * lim else 'OK')))
    nc = b[b['asset_class'] != 'Cash']
    for _, r in nc.iterrows():
        lim = LIMITS['single_name_max'] if r['single'] else LIMITS['fund_max']
        if r['w'] > 0.85 * lim:
            add('%s (%s)' % (r['ticker'], 'single name' if r['single'] else 'fund'), r['w'], lim)
    add('Top 5 positions', nc['w'].nlargest(5).sum(), LIMITS['top5_max'])
    for ac, lim in LIMITS['asset_class_max'].items():
        add('Asset class: %s' % ac, b.loc[b['asset_class'] == ac, 'w'].sum(), lim)
    add('Non-%s currency' % BASE_CCY, b.loc[b['ccy'] != BASE_CCY, 'w'].sum(), LIMITS['non_base_ccy_max'])
    return out


def signals(MS, b, checks):
    """Mechanical flags. Each: (scope, asset, tag, detail, prompt)."""
    out = []
    u = {'y': 'bp', 's': 'bp', 'p': '%', 'v': 'pts'}
    for t, st in MS.items():
        k, lab = KIND.get(t, 'p'), LABEL.get(t, t)
        if st['z5'] == st['z5'] and abs(st['z5']) >= Z_STRETCH:
            up = st['z5'] > 0
            if k == 'y':
                pr = 'Sell-off stretched: look at adding duration / receiving, or check what repriced.' if up else 'Rally stretched: look at trimming duration, or check what repriced.'
            elif k == 's':
                pr = 'Spread move stretched (%s): fade candidate if no new catalyst.' % ('wider/steeper' if up else 'tighter/flatter')
            elif k == 'v':
                pr = 'Vol spike: check hedges and gross exposure.' if up else 'Vol compressed: optionality is cheap relative to the last year.'
            else:
                pr = 'Extended rally: trim / take-profit candidate.' if up else 'Sharp drop: buy-the-dip or stop-out decision.'
            out.append(('MKT', lab, 'STRETCH', '5D %+.1f%s, z %+.1f' % (st['d5'], u[k], st['z5']), pr))
        if st['pctile'] >= 0.98 or st['pctile'] <= 0.02:
            top = st['pctile'] >= 0.98
            out.append(('MKT', lab, '1Y HIGH' if top else '1Y LOW', 'Last %s vs 1Y range %s to %s' % (fmt_last(st['last'], k), fmt_last(st['lo'], k), fmt_last(st['hi'], k)),
                        'At the edge of its 1Y range: breakout or reversal level to watch.'))
        if st['cross']:
            out.append(('MKT', lab, 'TREND', 'Crossed %s 50D average in the last week' % ('above' if st['cross'] > 0 else 'below'),
                        'Trend change: review exposure in the same direction.'))
    for name, val, lim, status in checks:
        if status != 'OK':
            out.append(('BOOK', name, status, '%.1f%% of NAV vs %.0f%% limit' % (val * 100, lim * 100),
                        'Over limit: trim.' if status == 'BREACH' else 'Close to limit: no room to add.'))
    for _, r in b.iterrows():
        if r['asset_class'] == 'Cash':
            continue
        if r['dd'] <= DRAWDOWN_FLAG:
            out.append(('BOOK', r['ticker'], 'DRAWDOWN', '%.1f%% below 3M high, %.1f%% of NAV' % (r['dd'] * 100, r['w'] * 100),
                        'Thesis check: add, hold or cut?'))
        if r['z5'] == r['z5'] and abs(r['z5']) >= Z_STRETCH:
            out.append(('BOOK', r['ticker'], 'BIG MOVE', '5D z %+.1f, %.1f%% of NAV' % (r['z5'], r['w'] * 100),
                        'Unusual move in a holding: rebalance or review.'))
    order = {'BREACH': 0, 'WARN': 1, 'DRAWDOWN': 2, 'BIG MOVE': 3, 'STRETCH': 4, '1Y HIGH': 5, '1Y LOW': 5, 'TREND': 6}
    return sorted(out, key=lambda x: (x[0] != 'BOOK', order.get(x[2], 9)))

# %%
# ============================== RENDERING ==============================
import ipywidgets as W
from IPython.display import display, clear_output
import plotly.graph_objects as go
from plotly.subplots import make_subplots

AMB, UP, DN, CY, MUT, WH = '#ffa028', '#00d75f', '#ff433d', '#4dc3ff', '#8a8a8a', '#e8e8e8'
MONO = "Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New', monospace"
CSS = """<style>
.bbg{background:#000;color:#e8e8e8;font-family:%(m)s;font-size:12px;line-height:1.35;padding:4px 6px;box-sizing:border-box}
.bbg table{border-collapse:collapse;width:100%%;font-variant-numeric:tabular-nums}
.bbg th{color:#ffa028;text-align:right;font-weight:normal;border-bottom:1px solid #ffa028;padding:2px 4px;font-size:11px;text-transform:uppercase;white-space:nowrap}
.bbg td{text-align:right;padding:1px 4px;white-space:nowrap;border-bottom:1px solid #161616;color:#e8e8e8}
.bbg th.l,.bbg td.l{text-align:left}
.bbg td.w{white-space:normal;text-align:left;min-width:220px}
.bbg tr:hover td{background:#10202f}
.bbg .up{color:#00d75f}.bbg .dn{color:#ff433d}.bbg .mut{color:#8a8a8a}.bbg .amb{color:#ffa028}.bbg .cy{color:#4dc3ff}
.bbg .sec{background:#0b2a4a;color:#ffa028;padding:2px 6px;margin:8px 0 2px;font-weight:bold;letter-spacing:.06em;text-transform:uppercase}
.bbg .top{display:flex;flex-wrap:wrap;gap:4px 18px;align-items:baseline;border-bottom:2px solid #ffa028;padding-bottom:3px}
.bbg .top b{color:#ffa028;font-size:15px;letter-spacing:.08em}
.bbg .tag{padding:0 5px;color:#000;font-weight:bold}
.bbg .kpis{display:flex;flex-wrap:wrap;gap:6px 28px;margin:6px 0}
.bbg .kpi span{display:block;color:#ffa028;font-size:10px;text-transform:uppercase;letter-spacing:.06em}
.bbg .kpi b{font-size:18px;font-weight:normal}
.bbg .scroll{overflow-x:auto}
.bbg .bar{display:inline-block;height:9px;vertical-align:middle}
.bbg .note{color:#8a8a8a;margin:4px 0}
.bbg-app,.bbg-app .widget-tab-contents,.bbg-app .widget-tab,.bbg-app .jupyter-widgets{background:#000 !important;color:#e8e8e8}
.bbg-app .widget-tab-contents{border:1px solid #333 !important;padding:4px !important}
.bbg-app .lm-TabBar-tab,.bbg-app .p-TabBar-tab{background:#141414 !important;color:#ffa028 !important;border:1px solid #333 !important;font-family:%(m)s;font-size:12px}
.bbg-app .lm-TabBar-tab.lm-mod-current,.bbg-app .p-TabBar-tab.p-mod-current{background:#ffa028 !important;color:#000 !important}
.bbg-app .lm-TabBar-tab.lm-mod-current:before,.bbg-app .p-TabBar-tab.p-mod-current:before{background:#ffa028 !important}
.bbg-app .widget-button{background:#141414;color:#ffa028;border:1px solid #ffa028;font-family:%(m)s;font-size:12px}
.bbg-app .widget-button:hover{background:#ffa028;color:#000}
.bbg-app .widget-label,.bbg-app label{color:#ffa028 !important;font-family:%(m)s;font-size:12px}
.bbg-app select,.bbg-app .widget-toggle-button{background:#141414 !important;color:#e8e8e8 !important;border:1px solid #444 !important;font-family:%(m)s;font-size:12px}
.bbg-app .widget-toggle-button.mod-active{background:#ffa028 !important;color:#000 !important}
</style>""" % {'m': MONO}


def esc(x):
    return html.escape(str(x))


def fmt_last(v, k):
    if v != v:
        return '-'
    if k == 'y':
        return '%.3f' % v
    if k == 's':
        return '%.1f' % v
    return '{:,.2f}'.format(v)


def fmt_chg(v, k):
    if v != v:
        return '<td class="mut">-</td>'
    txt = ('%+.2f%%' % v) if k == 'p' else (('%+.2f' % v) if k == 'v' else '%+.1f' % v)
    return '<td class="%s">%s</td>' % ('up' if v > 0 else ('dn' if v < 0 else 'mut'), txt)


def money(v, sign=False, cls=True):
    if v != v:
        return '<span class="mut">-</span>'
    txt = ('{:+,.0f}' if sign else '{:,.0f}').format(v)
    if not cls:
        return txt
    return '<span class="%s">%s</span>' % ('up' if v > 0 else ('dn' if v < 0 else 'mut'), txt) if sign else txt


def pct(v, sign=True, d=1):
    if v != v:
        return '<span class="mut">-</span>'
    txt = ('%+.' + str(d) + 'f%%' if sign else '%.' + str(d) + 'f%%') % v
    return '<span class="%s">%s</span>' % ('up' if v > 0 else ('dn' if v < 0 else 'mut'), txt) if sign else txt


def spark(s, w=56, h=14):
    v = s.dropna().values[-60:]
    if len(v) < 2:
        return ''
    lo, hi = v.min(), v.max()
    rg = (hi - lo) or 1.0
    pts = ' '.join('%.1f,%.1f' % (i * w / (len(v) - 1.0), h - 1 - (x - lo) / rg * (h - 2)) for i, x in enumerate(v))
    return '<svg width="%d" height="%d"><polyline points="%s" fill="none" stroke="%s" stroke-width="1"/></svg>' % (w, h, pts, UP if v[-1] >= v[0] else DN)


def rangebar(p, w=48, h=10):
    x = 1 + max(0.0, min(1.0, p)) * (w - 2)
    return ('<svg width="%d" height="%d"><line x1="0" y1="%d" x2="%d" y2="%d" stroke="#555" stroke-width="1"/>'
            '<rect x="%.1f" y="1" width="3" height="%d" fill="%s"/></svg>') % (w, h, h // 2, w, h // 2, x - 1.5, h - 2, AMB)


def monitor_table(title, keys, MS, unit):
    h = ['<div class="sec">%s</div><div class="scroll"><table><tr><th class="l">%s</th><th>Last</th><th>1D</th><th>5D</th><th>1M</th><th>YTD</th>'
         '<th>1Y range</th><th>60D</th><th>Z 5D</th></tr>' % (esc(title), esc(unit))]
    for t in keys:
        st = MS.get(t)
        if not st:
            h.append('<tr><td class="l amb">%s</td><td class="mut" colspan="8">no data</td></tr>' % esc(LABEL.get(t, t)))
            continue
        k = KIND.get(t, 'p')
        z = st['z5']
        zc = 'mut' if z != z or abs(z) < Z_STRETCH else ('up' if z > 0 else 'dn')
        h.append('<tr><td class="l amb">%s</td><td>%s</td>%s%s%s%s<td>%s</td><td>%s</td><td class="%s">%s</td></tr>' % (
            esc(LABEL.get(t, t)), fmt_last(st['last'], k), fmt_chg(st['d1'], k), fmt_chg(st['d5'], k), fmt_chg(st['d21'], k),
            fmt_chg(st['ytd'], k), rangebar(st['pctile']), spark(st['s']), zc, '-' if z != z else '%+.1f' % z))
    h.append('</table></div>')
    return ''.join(h)


def header_html(D):
    mode = '<span class="tag" style="background:%s">%s</span>' % ((UP, LIVE_TAG) if LIVE else (DN, 'SAMPLE DATA - NOT MARKET PRICES'))
    bk = '<span class="tag" style="background:%s">BOOK: %s</span>' % (DN if D['pos_src'] == 'SAMPLE BOOK' else UP, esc(D['pos_src']))
    miss = ' <span class="dn">no data: %s</span>' % esc(', '.join(D['missing'][:6])) if D['missing'] else ''
    last = D['mon'].index[-1].strftime('%d %b %Y')
    if D.get('note'):
        miss += ' <span class="mut">%s</span>' % esc(D['note'])
    return ('<div class="top"><b>PA TERMINAL &lt;GO&gt;</b>%s %s<span class="mut">prices to %s &middot; pulled %s &middot; base %s</span>%s</div>'
            % (mode, bk, last, D['asof'].strftime('%H:%M:%S'), BASE_CCY, miss))


def kpis_html(b, nav, R):
    def k(lab, val):
        return '<div class="kpi"><span>%s</span><b>%s</b></div>' % (lab, val)
    inv = b.loc[b['asset_class'] != 'Cash', 'mv'].sum()
    return '<div class="kpis">' + ''.join([
        k('NAV (%s)' % BASE_CCY, money(nav)), k('Day P&L', money(b['pd1'].sum(), True) + ' ' + pct(b['pd1'].sum() / nav * 100, d=2)),
        k('MTD P&L', money(b['pmtd'].sum(), True)), k('YTD P&L', money(b['pytd'].sum(), True)),
        k('Unrealised', money(b['upl'].sum(), True)), k('Invested', pct(inv / nav * 100, False)),
        k('Beta to %s' % esc(LABEL.get(BETA_REF, BETA_REF)), '-' if R['beta'] != R['beta'] else '%.2f' % R['beta']),
        k('Vol (ann.)', pct(R['vol'] * 100, False)), k('1D VaR 95%', money(R['var95'])),
        k('P&L per +1%s %s' % ('bp' if KIND.get(RATE_REF) == 'y' else '%', esc(LABEL.get(RATE_REF, RATE_REF))), money(R['bp10'], True))]) + '</div>'


def blotter_html(b, nav):
    h = ['<div class="sec">Positions</div><div class="scroll"><table><tr><th class="l">Ticker</th><th class="l">Name</th><th class="l">Class</th><th>Qty</th>'
         '<th>Px</th><th>1D</th><th>Mkt val</th><th>Wt</th><th>Day P&L</th><th>MTD</th><th>YTD</th><th>Unrl P&L</th><th>Unrl %</th><th>vs 3M hi</th></tr>']
    for _, r in b.iterrows():
        cash = r['asset_class'] == 'Cash'
        h.append('<tr><td class="l amb">%s</td><td class="l">%s</td><td class="l mut">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td>'
                 '<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                     esc(r['ticker']), esc(r['name']), esc(r['asset_class']), '{:,.0f}'.format(r['qty']),
                     '-' if cash else '{:,.2f}'.format(r['px']), '-' if cash else pct(r['d1p'], d=2), money(r['mv']), r['w'] * 100,
                     money(r['pd1'], True), money(r['pmtd'], True), money(r['pytd'], True), money(r['upl'], True),
                     '-' if cash or not r['cost'] else pct(r['upl'] / r['cost'] * 100), '-' if cash else pct(r['dd'] * 100)))
    h.append('<tr><td class="l amb">TOTAL</td><td></td><td></td><td></td><td></td><td></td><td>%s</td><td>100.0%%</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td></td><td></td></tr>'
             % (money(nav), money(b['pd1'].sum(), True), money(b['pmtd'].sum(), True), money(b['pytd'].sum(), True), money(b['upl'].sum(), True)))
    h.append('</table></div><div class="note">P&L is price-only on current holdings (no dividends, coupons, fees or closed trades). '
             '%s</div>' % esc(BOOK_NOTE))
    return ''.join(h)


def exposure_html(b, checks):
    def bars(title, grp, limits=None):
        g = b.groupby(grp)['mv'].sum().sort_values(ascending=False)
        tot = g.sum()
        h = ['<div class="sec">%s</div><table><tr><th class="l">%s</th><th>Mkt val</th><th>Wt</th><th class="l">&nbsp;</th><th>Limit</th></tr>' % (title, grp.replace('_', ' '))]
        for kx, v in g.items():
            w = v / tot
            lim = (limits or {}).get(kx)
            col = DN if lim and w > lim else (AMB if lim and w > 0.85 * lim else CY)
            h.append('<tr><td class="l amb">%s</td><td>%s</td><td>%.1f%%</td><td class="l"><span class="bar" style="width:%dpx;background:%s"></span></td><td class="mut">%s</td></tr>'
                     % (esc(kx), money(v), w * 100, max(1, int(w * 220)), col, '%.0f%%' % (lim * 100) if lim else '-'))
        h.append('</table>')
        return ''.join(h)
    h = [bars('Exposure by asset class', 'asset_class', LIMITS['asset_class_max']), bars('Exposure by currency', 'ccy')]
    h.append('<div class="sec">Limits</div><table><tr><th class="l">Limit</th><th>Current</th><th>Limit</th><th>Headroom</th><th>Status</th></tr>')
    for name, val, lim, status in checks:
        col = {'OK': UP, 'WARN': AMB, 'BREACH': DN}[status]
        h.append('<tr><td class="l amb">%s</td><td>%.1f%%</td><td>%.0f%%</td><td>%+.1f%%</td><td><span class="tag" style="background:%s">%s</span></td></tr>'
                 % (esc(name), val * 100, lim * 100, (lim - val) * 100, col, status))
    h.append('</table><div class="note">Single-name and fund lines appear once they pass 85% of their limit.</div>')
    nc = b[b['asset_class'] != 'Cash']
    hhi = float((nc['w'] ** 2).sum())
    h.append('<div class="sec">Concentration</div><table><tr><th class="l">Top positions</th><th>Wt</th><th>Cumulative</th></tr>')
    cum = 0.0
    for _, r in nc.head(5).iterrows():
        cum += r['w']
        h.append('<tr><td class="l amb">%s</td><td>%.1f%%</td><td>%.1f%%</td></tr>' % (esc(r['ticker']), r['w'] * 100, cum * 100))
    h.append('</table><div class="note">Herfindahl index %.3f, equivalent to %.1f equal-sized positions.</div>' % (hhi, 1 / hhi if hhi else 0))
    return ''.join(h)


def events_html(ev, notes):
    h = ['<div class="sec">Events: next %d days</div>' % EVENT_DAYS]
    if len(ev):
        h.append('<table><tr><th class="l">Date</th><th class="l">Region</th><th class="l">Event</th><th class="l">Imp</th><th class="l">Src</th></tr>')
        for _, r in ev.iterrows():
            h.append('<tr><td class="l amb">%s</td><td class="l">%s</td><td class="l">%s</td><td class="l %s">%s</td><td class="l mut">%s</td></tr>' % (
                r['date'].strftime('%a %d %b'), esc(r['region']), esc(r['event']), 'dn' if r['importance'] == 'HIGH' else 'mut', esc(r['importance']), esc(r['src'])))
        h.append('</table>')
    else:
        h.append('<div class="note">No events found for the window.</div>')
    for n in notes:
        h.append('<div class="note">%s</div>' % esc(n))
    return ''.join(h)


def signals_html(sig):
    h = ['<div class="sec">Flags and idea prompts</div>']
    if not sig:
        return h[0] + '<div class="note">Nothing flagged.</div>'
    h.append('<div><table><tr><th class="l">Scope</th><th class="l">Asset</th><th class="l">Flag</th><th class="l">Detail and prompt</th></tr>')
    col = {'BREACH': DN, 'WARN': AMB, 'DRAWDOWN': DN, 'BIG MOVE': AMB, 'STRETCH': CY, '1Y HIGH': UP, '1Y LOW': DN, 'TREND': WH}
    for sc, a, tag, det, pr in sig:
        h.append('<tr><td class="l mut">%s</td><td class="l amb">%s</td><td class="l"><span class="tag" style="background:%s">%s</span></td><td class="w">%s<br><span class="mut">%s</span></td></tr>'
                 % (sc, esc(a), col.get(tag, MUT), tag, esc(det), esc(pr)))
    h.append('</table></div><div class="note">Mechanical screen: z-scores of 5D moves against the last year, 1Y range edges, 50D average crosses, and your limits. Not investment advice.</div>')
    return ''.join(h)


def movers_html(MS):
    rows = [(LABEL.get(t, t), KIND.get(t, 'p'), st) for t, st in MS.items() if st['z5'] == st['z5']]
    rows.sort(key=lambda x: -abs(x[2]['z5']))
    h = ['<div class="sec">Biggest 5D moves (by z-score)</div><table><tr><th class="l">Asset</th><th>Last</th><th>5D</th><th>Z 5D</th></tr>']
    for lab, k, st in rows[:10]:
        h.append('<tr><td class="l amb">%s</td><td>%s</td>%s<td class="%s">%+.1f</td></tr>' % (esc(lab), fmt_last(st['last'], k), fmt_chg(st['d5'], k), 'up' if st['z5'] > 0 else 'dn', st['z5']))
    h.append('</table><div class="note">5D change: bp for yields and spreads, % for prices, points for vol indices.</div>')
    return ''.join(h)


def themed(fig, h=300, title=None):
    fig.update_layout(template=None, paper_bgcolor='#000', plot_bgcolor='#000', font=dict(family=MONO, color='#c8c8c8', size=11),
                      margin=dict(l=55, r=20, t=40, b=30), height=h, title=dict(text=title, font=dict(color=AMB, size=12), x=0.01),
                      legend=dict(orientation='h', y=1.14, x=1, xanchor='right', bgcolor='rgba(0,0,0,0)'), hovermode='x unified',
                      hoverlabel=dict(bgcolor='#111', font=dict(family=MONO, color=WH)))
    fig.update_xaxes(gridcolor='#1c1c1c', zeroline=False, linecolor='#444')
    fig.update_yaxes(gridcolor='#1c1c1c', zerolinecolor='#555', linecolor='#444')
    return fig


def price_fig(MS, t, days):
    st = MS[t]
    s = st['s']
    ma = s.rolling(50).mean().iloc[-days:]
    s = s.iloc[-days:]
    unit = {'y': 'yield, %', 's': 'bp', 'p': 'price', 'v': 'index'}[KIND.get(t, 'p')]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.index, y=s.values, name=LABEL.get(t, t), line=dict(color=AMB, width=1.6)))
    fig.add_trace(go.Scatter(x=ma.index, y=ma.values, name='50D avg', line=dict(color=CY, width=1, dash='dot')))
    return themed(fig, 320, '%s (%s)' % (LABEL.get(t, t), unit))


def curves_fig(mon):
    if not CURVES:
        return None
    fig = make_subplots(rows=1, cols=len(CURVES), subplot_titles=['%s curve' % c for c in CURVES])
    for i, (c, pts) in enumerate(CURVES.items()):
        pts = [(n, t) for n, t in pts if t in mon]
        for lab, back, col, dash in [('Now', 0, AMB, None), ('1W ago', 5, WH, 'dot'), ('1M ago', 21, CY, 'dot')]:
            if len(mon) <= back or not pts:
                continue
            row = mon.iloc[-1 - back]
            fig.add_trace(go.Scatter(x=['%dY' % n for n, _ in pts], y=[row[t] for _, t in pts], name=lab, legendgroup=lab, showlegend=(i == 0),
                                     mode='lines+markers', line=dict(color=col, width=1.6 if back == 0 else 1, dash=dash), marker=dict(size=5)),
                          row=1, col=i + 1)
    themed(fig, 280, 'Government curves: yield, %')
    fig.update_layout(hovermode='closest', margin=dict(l=55, r=20, t=70, b=30), legend=dict(y=1.32))
    fig.update_annotations(font=dict(color=AMB, size=11, family=MONO))
    return fig


def contrib_fig(b, col, lab):
    d = b[b['asset_class'] != 'Cash'].sort_values(col)
    fig = go.Figure(go.Bar(x=d[col], y=d['ticker'], orientation='h', marker_color=[UP if v >= 0 else DN for v in d[col]],
                           text=['{:+,.0f}'.format(v) for v in d[col]], textposition='outside', textfont=dict(color=WH), cliponaxis=False))
    themed(fig, 60 + 24 * len(d), '%s P&L by position (%s)' % (lab, BASE_CCY))
    fig.update_layout(hovermode='closest', margin=dict(l=130, r=70, t=40, b=30))
    return fig


def nav_fig(nav_hist):
    s = nav_hist.dropna()
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, name='NAV', line=dict(color=AMB, width=1.6), fill='tozeroy', fillcolor='rgba(255,160,40,0.08)'))
    themed(fig, 300, 'Current holdings back-cast (%s): what today\'s book would have been worth' % BASE_CCY)
    fig.update_yaxes(range=[s.min() * 0.97, s.max() * 1.02])
    return fig

# %%
# ============================== APP ==============================
class Terminal(object):
    PERIODS = {'1M': 21, '3M': 63, '6M': 126, '1Y': 260}

    def __init__(self):
        self.h_top, self.h_rates, self.h_mid, self.h_cmd = W.HTML(), W.HTML(), W.HTML(), W.HTML()
        self.h_kpi, self.h_blot, self.h_risk, self.h_week_l, self.h_week_r = W.HTML(), W.HTML(), W.HTML(), W.HTML(), W.HTML()
        self.o_px, self.o_curve, self.o_contrib, self.o_nav = W.Output(), W.Output(), W.Output(), W.Output()
        self.status = W.HTML()
        self.dd = W.Dropdown(description='Security', layout=W.Layout(width='300px'))
        self.per = W.ToggleButtons(options=list(self.PERIODS), value='6M', style={'button_width': '48px'})
        self.pnl_per = W.ToggleButtons(options=['Day', 'MTD', 'YTD'], value='MTD', style={'button_width': '56px'})
        self.b_ref = W.Button(description='REFRESH', layout=W.Layout(width='110px'))
        self.b_exp = W.Button(description='EXPORT WEEKLY BRIEF', layout=W.Layout(width='200px'))
        self.b_ref.on_click(lambda _: self.refresh())
        self.b_exp.on_click(lambda _: self.export())
        self.dd.observe(lambda c: self.draw_px(), names='value')
        self.per.observe(lambda c: self.draw_px(), names='value')
        self.pnl_per.observe(lambda c: self.draw_contrib(), names='value')
        wrap = W.Layout(display='flex', flex_flow='row wrap', width='100%')
        col = lambda *k: W.VBox(list(k), layout=W.Layout(flex='1 1 420px', min_width='0', max_width='100%'))
        t_mon = W.VBox([W.Box([col(self.h_rates), col(self.h_mid), col(self.h_cmd)], layout=wrap), W.HBox([self.dd, self.per]), self.o_px, self.o_curve])
        t_book = W.VBox([self.h_kpi, self.h_blot, self.pnl_per, W.Box([col(self.o_contrib), col(self.o_nav)], layout=wrap)])
        t_week = W.VBox([W.Box([col(self.h_week_l), col(self.h_week_r)], layout=wrap)])
        self.tabs = W.Tab(children=[t_mon, t_book, self.h_risk, t_week])
        for i, n in enumerate(['1) MONITOR', '2) PA BOOK', '3) RISK & LIMITS', '4) WEEK AHEAD']):
            self.tabs.set_title(i, n)
        self.ui = W.VBox([W.HTML(CSS), self.h_top, W.HBox([self.b_ref, self.b_exp, self.status]), self.tabs], layout=W.Layout(width='100%'))
        self.ui.add_class('bbg-app')

    @staticmethod
    def _wrap(x):
        return '<div class="bbg">%s</div>' % x

    def _show(self, out, fig):
        with out:
            clear_output(wait=True)
            if fig is not None:
                display(fig)

    def refresh(self):
        self.status.value = self._wrap('<span class="amb">Loading...</span>')
        try:
            self.D = load_all()
            self.MS = monitor_stats(self.D['mon'])
            self.b, self.nav, self.nav_hist = book(self.D)
            self.R = risk(self.b, self.nav, self.nav_hist, self.D['mon'])
            self.checks = limit_checks(self.b)
            self.sig = signals(self.MS, self.b, self.checks)
            self.ev, self.ev_notes = load_events()
        except Exception as e:
            self.status.value = self._wrap('<span class="dn">Load failed: %s</span>' % esc(e))
            raise
        self.render()
        self.status.value = ''

    def render(self):
        MS, w = self.MS, self._wrap
        self.h_top.value = w(header_html(self.D))
        self.h_rates.value = w(monitor_table('Rates', [t for t, _, _ in RATES], MS, RATES_UNIT))
        self.h_mid.value = w(monitor_table(SPREAD_TITLE, SPREAD_KEYS, MS, SPREAD_UNIT) +
                             monitor_table('Equities', [t for t, _, _ in EQUITIES], MS, 'Index / chg %'))
        self.h_cmd.value = w(monitor_table('Commodities and crypto', [t for t, _, _ in COMMOD], MS, 'Price / chg %') + movers_html(MS))
        self.h_kpi.value = w(kpis_html(self.b, self.nav, self.R))
        self.h_blot.value = w(blotter_html(self.b, self.nav))
        self.h_risk.value = w(kpis_html(self.b, self.nav, self.R) + exposure_html(self.b, self.checks) +
                              '<div class="note">Beta, vol, VaR and the rate sensitivity are estimated from one year of daily moves of the current holdings. '
                              'The rate sensitivity is a regression estimate, not a bond-maths DV01.</div>')
        self.h_week_l.value = w(events_html(self.ev, self.ev_notes) + movers_html(MS))
        self.h_week_r.value = w(signals_html(self.sig))
        opts = [(LABEL.get(t, t), t) for t in list(MS)]
        cur = self.dd.value if self.dd.value in MS else (RATE_REF if RATE_REF in MS else opts[0][1])
        self.dd.options = opts
        self.dd.value = cur
        self.draw_px()
        self._show(self.o_curve, curves_fig(self.D['mon']))
        self.draw_contrib()
        self._show(self.o_nav, nav_fig(self.nav_hist))

    def draw_px(self):
        if getattr(self, 'MS', None) and self.dd.value in self.MS:
            self._show(self.o_px, price_fig(self.MS, self.dd.value, self.PERIODS[self.per.value]))

    def draw_contrib(self):
        if getattr(self, 'b', None) is not None:
            c = {'Day': 'pd1', 'MTD': 'pmtd', 'YTD': 'pytd'}[self.pnl_per.value]
            self._show(self.o_contrib, contrib_fig(self.b, c, self.pnl_per.value))

    def brief_html(self):
        body = (header_html(self.D) + kpis_html(self.b, self.nav, self.R) + events_html(self.ev, self.ev_notes) + signals_html(self.sig) +
                movers_html(self.MS) + blotter_html(self.b, self.nav) + exposure_html(self.b, self.checks))
        return ('<!doctype html><html><head><meta charset="utf-8"><title>PA weekly brief %s</title>%s</head>'
                '<body style="background:#000;margin:0"><div class="bbg" style="max-width:1100px;margin:auto">%s</div></body></html>'
                % (dt.date.today().isoformat(), CSS, body))

    def export(self):
        fn = 'pa_weekly_brief_%s.html' % dt.date.today().strftime('%Y%m%d')
        with io.open(fn, 'w', encoding='utf-8') as f:
            f.write(self.brief_html())
        self.status.value = self._wrap('<span class="up">Wrote %s</span>' % fn)


app = Terminal()
display(app.ui)
app.refresh()
