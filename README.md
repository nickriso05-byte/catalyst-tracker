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
| `app.py` | The Streamlit app, including the scheduled refresher |
| `build.py` | Data engine: SEC full-text search, catalyst extraction, Unusual Whales and EDGAR pulls. Also runs standalone: `python build.py` |
| `card.py` | HTML for the company card, in the Catalyst Trader member-site styling |
| `gate.py` | Password screen (APP_PASSWORD) |
| `theme.css` | Theme tokens taken from the member site |
| `public/index.html` | An earlier static-site version (not used by the app; gitignored) |

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
The first calendar build takes about 15 to 20 minutes, because it reads around 3,000 SEC filings.
Company lookups work right away. After that, refreshes are faster: filing text is cached, and
quarterly financials and 13F holdings are cached for 2 to 3 days.

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
