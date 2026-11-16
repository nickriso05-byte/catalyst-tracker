"""
verdad_fund_ciks.py — single source of truth for the Verdad screener's fund universe.
Place in the SAME directory as fetch_13f.py. fetch_13f.py does:
    from verdad_fund_ciks import TIER1_FUNDS, TIER2_FUNDS

Tier = SCREENER WEIGHT, not roster priority:
    TIER1_FUNDS -> W_TIER1 (100 pts/fund)   TIER2_FUNDS -> W_TIER2 (40 pts/fund)
Names are SHORT on purpose (screener prints them in 35-40 char columns).
CIKs verified against each manager's 13F-HR filer record (Jun 2026).
"""

# -- TIER 1 (100 pts/fund): large dedicated biopharma specialists --------------
TIER1_FUNDS = {
    # original core (unchanged)
    "Baker Bros":    "1263508",
    "Perceptive":    "1224962",
    "RA Capital":    "1346824",
    "OrbiMed":       "1055951",
    "Casdin":        "1534261",
    "Cormorant":     "1583977",
    "BVF":           "1056807",
    "EcoR1":         "1587114",
    "RTW":           "1493215",
    "Redmile":       "1425738",
    "Deerfield":     "1009258",
    "Commodore":     "1831942",
    # new (roster Tier A)
    "Avoro":         "1633313",   # filer alt name: venBio Select
    "Logos":         "1792126",   # active filer (older 1172661 also exists)
    "Fairmount":     "1802528",
    "Soleus":        "1802630",
    "Rock Springs":  "1595725",
    "Sphera":        "1496201",
    "Deep Track":    "1856083",
    "TCG Crossover": "1839948",
    "Braidwell":     "1920938",
    "Paradigm Bio":  "1855655",
    "Samsara":       "1744967",
    "Vivo":          "1674712",
    "Frazier":       "1892134",
    "Bain LS":       "1703031",   # combined filer covering all BCLS sub-funds
}

# -- TIER 2 (40 pts/fund): smaller / activist / microcap / noisier -------------
TIER2_FUNDS = {
    "Boxer Capital": "2018299",
    "Suvretta":      "1569064",   # multi-strat; filing carries non-biotech names too
    "Acuta":         "1582844",
    "Tang":          "1232621",
    "Sarissa":       "1577524",   # activist
    "Ghost Tree":    "1595851",
    "Opaleye":       "1595855",   # microcap specialist -- consider promoting to TIER1
    "Great Point":   "1281446",
    "Rosalind":      "1622627",   # microcap specialist -- consider promoting to TIER1
    "Vestal Point":  "1974915",
    "Stonepine":     "1440771",   # ~$100M -- drop if strict on $250M AUM
    "Velan":         "1848809",   # ~$100M activist -- drop if strict
    "Tekla":         "1300336",   # diversified CEF complex; low catalyst signal
}

# Excluded: Consonance (1544773, wound down); Foresite (fragmented across
#   series II 1594912 / IV 1704132 -- add to TIER2 manually if you want it).

ALL_FUNDS = {**TIER1_FUNDS, **TIER2_FUNDS}
