# FDA Catalyst Tracker (for Ray Blanco's Catalyst Trader)

A Streamlit app with three parts:
- a calendar of upcoming FDA and biotech catalysts
- screens such as squeeze watch, insider buying, new 5%+ holders and cash risk
- a live company card for any healthcare stock: cash runway, short interest, insider trades,
  13D/13G holders, institutions, share-sale filings, and the options-implied move into the event

## How it stays fresh
- **Scheduled refresh:** a background thread in the app rebuilds the calendar every 30 minutes,
  Monday to Friday from 6 AM to 8 PM Eastern, and every 3 hours otherwise. The **Refresh calendar now**
  button runs it on demand. Visitors keep seeing the last good snapshot while it runs.
- **Live on click:** opening a company card re-pulls that company right then, including price,
  insider trades, 13D/13G, share-sale filings and options. Any SEC filings made since the last refresh
  are read for new catalysts and flagged "New filing". Cards are re-used for 5 minutes.

## Files
| File | What it is |
|---|---|
| `app.py` | Thin Streamlit shell: password, scheduled refresher, live company cards, and mounting the UI |
| `ui/ui.html`, `ui/ui.css`, `ui/ui.js` | The interface (calendar, screens, search, slide-in company card), mounted as a Streamlit v2 component. Clicking a company sends an "open" trigger to Python, which pulls the live card and passes it back. No page reloads. |
| `build.py` | Data engine: SEC full-text search, catalyst extraction, Unusual Whales and EDGAR pulls. Also runs standalone: `python build.py` |
| `gate.py` | Password screen (APP_PASSWORD), remembered for a year per browser |
| `theme.css` | Theme tokens taken from the Catalyst Trader member site |
| `cache/docs/` | Catalyst sentences already read from SEC filings, shipped so a fresh server starts fast |

## Run locally
```
streamlit run app.py
```
Run it from this folder so `.streamlit/config.toml` (the theme) is picked up. The Desktop launch config
`catalyst-app` does this on port 8541. On localhost the password screen is skipped when no APP_PASSWORD is set.

## Secrets (Streamlit Cloud > App settings > Secrets, or local .streamlit/secrets.toml)
```
UW_API_TOKEN = "..."
APP_PASSWORD = "..."
SEC_USER_AGENT = "Paradigm Press Catalyst Tracker contact@yourdomain.com"
```
The SEC asks automated clients to identify themselves with a real contact.

## On a fresh server
The calendar shows about 20 to 30 seconds after a fresh start, and cash runway, implied moves and 13D/13G fill in about a minute later. The SEC filing cache (cache/docs) ships in the repo, so only filings newer than the last push are read.
Company lookups work right away. Regular refreshes only look at what changed (new filings, prices, short interest, insider buys);
quarterly financials and 13F holdings are reused for 2 to 3 days. A normal refresh takes about 25 seconds.

## Verdad-style score (built in)
Every company gets a 0–100 score from the factors Verdad's "Biotech Investing" paper found predictive,
recomputed on every refresh and live when a card opens:
- **Top-tier specialist funds:** how many of the 26 tier-1 biotech funds hold it (share of stock breaks ties).
  Uses each fund's latest 13F, Q2 or Q3, so funds don't drop out during filing season.
- **Short interest:** % of float (lower is better).
- **Insider buying:** open-market Form 4 buys in 90 days, relative to market cap.
- **Value vs. spending:** annual R&D + SG&A per dollar of enterprise value ex-cash (cheaper is better).
Each factor is ranked against the companies on the calendar and averaged equally. Peer momentum is left out
(Verdad defines peers by clinical-trial similarity). Not backtested yet.

## Colleague's scan (optional)
The app can show a colleague's daily Verdad-style biotech scan next to the catalysts: a **Score** column
(with rank), a **Verdad score** section on each company card (specialist funds, short interest, insiders,
scientific peers, value vs. spending, balance sheet), and a **Top-scored with a catalyst** screen.
It reads the scan CSV from `VERDAD_CSV_URL` in the secrets (for example a Google Sheet published as CSV),
or locally from `verdad/latest.csv`. The scan is never committed to this public repo.
Expected columns: ticker, score, tier1, tier2, tier1_funds, short_float, short_signal, insider_trans,
insider_signal, peer_mom, peer_signal, ev_to_burn, value_signal, current_ratio, cash_signal.

## Where the numbers come from
| Section | Source |
|---|---|
| Catalyst dates | Companies' SEC filings (8-K and press-release exhibits, 10-Q, 10-K, 6-K, 20-F) via EDGAR full-text search, plus the Unusual Whales FDA calendar |
| Price, market cap, options | Unusual Whales |
| Short interest | Unusual Whales (FINRA data, published twice a month, lags a few weeks) |
| Insider trades | Unusual Whales, Form 4 open-market buys (P) and sells (S) only |
| Cash runway | Unusual Whales quarterly financials: (cash + short-term investments) / average operating cash burn of the last 2 quarters |
| 13D / 13G, share-sale filings | SEC EDGAR |
| Institutions | Unusual Whales 13F, latest quarter. "Biotech fund" tags come from a hand-made list in `build.py` (SPECIALISTS) |

## Known limits
- Catalyst dates are read from filings by pattern matching, not by a person. Each one shows its source quote and link so it can be checked.
- Drug names are guessed from codes (ABC-123) and generic-name endings. Brand names are often missed.
- Unusual Whales' FDA calendar stopped updating around Oct 19, 2026, which is why SEC filings are the main source.
- Check with Unusual Whales before showing their data to paying subscribers (licensing).
- Streamlit Cloud apps go to sleep after a period with no visitors. On wake-up the calendar rebuilds (see "On a fresh server").
