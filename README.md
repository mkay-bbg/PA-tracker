# PA Tracker

A Bloomberg-terminal-style market monitor and personal-account positioning dashboard, as Jupyter notebooks.

| Notebook | Data source | Where it runs |
| --- | --- | --- |
| `PA_Terminal_Futu.ipynb` | Futu OpenAPI (positions and cash read from the account) | Jupyter or VS Code on the machine where Futu OpenD is logged in |
| `PA_Terminal.ipynb` | Bloomberg BQL (positions from `pa_positions.csv`) | Bloomberg BQuant |

Both fall back to random sample data, flagged in red in the header, when their data source is not reachable.

## Tabs

1. **Monitor**: rates, relative value, equities, commodities and crypto with 1D / 5D / 1M / YTD changes, 1Y range and a z-score of the 5-day move.
2. **PA book**: NAV, day / MTD / YTD and unrealised P&L, position blotter, P&L by position.
3. **Risk & limits**: exposure by asset class and currency, concentration, limit checks.
4. **Week ahead**: events from `pa_events.csv` and mechanical flags with idea prompts.

## Run the Futu version

Start Futu OpenD and log in, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install futu-api ipywidgets plotly pandas jupyterlab
jupyter lab
```

Open `PA_Terminal_Futu.ipynb`, set `SECURITY_FIRM` and `BASE_CCY` in the config cell, and run all cells. The notebook only makes read-only account queries; it never unlocks trading and has no order calls.

## Editing

The notebooks are generated. Edit the cells in `src/` and rebuild:

```bash
python3 src/build.py
```

`cells_bql.py` and `cells_futu.py` hold each data source's config and loader; `cells_common.py` holds the shared analytics, rendering and app.

## Notes

- `pa_positions.csv`, `pa_events.csv`, the Futu price cache and exported briefs are git-ignored so personal holdings stay local.
- P&L is price-only on current holdings. The flags are a rules-based screen, not investment advice.
