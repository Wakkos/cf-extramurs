# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Extramurs Calendar Automation** is a web scraping system that automatically scrapes the FFCV (Federación de Fútbol de la Comunidad Valenciana) website to generate:

- 📅 `.ics` calendar files for syncing with Google Calendar, iPhone, Outlook
- 🌐 Landing page with calendar subscription buttons
- 📊 Dashboard with team statistics, results, and standings
- 🤖 Daily automatic updates via GitHub Actions

**Team**: C.F. Extramurs Valencia 'B'
**Category**: Prebenjamín (Segona FFCV)
**Season**: 2024-2025

## Essential Commands

### Running the Scraper

```bash
# Install dependencies (first time only)
pip install -r requirements.txt
playwright install chromium

# Run main scraper
python scraper.py

# Debug scraper (saves raw HTML for inspection)
python debug_scraper.py
```

### Development Workflow

The scraper is designed to run automatically via GitHub Actions, but can be run locally for testing and debugging.

## Architecture

### Core Scraping Flow

1. **Playwright Browser Automation** (`fetch_page_with_retry`)
   - Uses headless Chromium to bypass FFCV's request blocking
   - Implements 3-retry logic with 5-second delays
   - Waits for "networkidle" to ensure full page load

2. **Data Extraction** (BeautifulSoup)
   - `scrape_calendario()`: Extracts matches from calendar page
   - `scrape_clasificacion()`: Extracts standings from classification page
   - Both parse specific HTML structures unique to FFCV's isquad system

3. **Data Processing**
   - `parse_spanish_date()`: Handles Spanish date formats (DD-MM-YYYY, long format)
   - `encontrar_proximo_partido()`: Identifies next upcoming match
   - Calculates win/loss/draw streaks from recent results

4. **Output Generation**
   - `generar_calendario_ics()`: Creates iCalendar (.ics) file using `ics` library
   - `generar_json()`: Saves structured data to `data/partidos.json`
   - `generar_html_desde_template()`: Renders Jinja2 templates to HTML

### File Structure

```
extramurs/
├── scraper.py                  # Main scraper (656 lines)
├── debug_scraper.py            # Debug tool to inspect HTML
├── requirements.txt            # Python dependencies
├── partidos.ics               # Generated calendar (auto)
├── index.html                 # Generated landing page (auto)
├── dashboard.html             # Generated dashboard (auto)
├── manifest.json              # PWA manifest
├── data/
│   └── partidos.json          # Structured match data (auto)
├── templates/
│   ├── index_template.html    # Jinja2 template for landing
│   └── dashboard_template.html # Jinja2 template for dashboard
└── .github/workflows/
    └── update.yml             # Daily automation workflow
```

## Key Implementation Details

### HTML Parsing Strategy

FFCV's HTML structure is brittle and subject to change. The scraper uses:

- **Calendar matches** (`scrape_calendario`, line 151):
  - Finds `<tr>` rows containing team name
  - Extracts: teams (links), result (spans), date/time (divs), field (td.negrita)
  - Generates Google Maps URLs for each field
  - Determines home/away and win/loss status

- **Standings** (`scrape_clasificacion`, line 292):
  - Locates `<table class="clasificacion">`
  - Parses position, team name, points, games played, wins/draws/losses
  - Identifies team position to display motivational message if in last place

### Date Parsing Complexity

Spanish dates come in multiple formats:
- "14-11-2025" (DD-MM-YYYY)
- "09/11/2025" (DD/MM/YYYY)
- "Sábado, 09 De Noviembre" (long format)

`parse_spanish_date()` (line 94) handles all formats, defaulting to year 2025 for current season.

### Template Context

Both templates receive:
- `equipo`, `grupo`: Team identification
- `proximo_partido`: Next match details with urgency flag (< 24h)
- `ultimos_resultados`: Last 5 match results
- `racha`: Win/loss streak as ['W', 'L', 'D'] array
- `clasificacion`: Full standings table
- `posicion_equipo`: Team's current position
- `mensaje_motivacional`: Optional encouragement if in last place
- `ics_url`, `webcal_url`, `google_calendar_url`: Subscription links

## GitHub Actions Workflow

### Schedule & Triggers

- **Daily**: 6:00 UTC (7:00 AM Madrid time, winter)
- **Manual**: Via "Run workflow" button in Actions tab
- **On push**: To `main` branch (for testing)

### Workflow Steps (`.github/workflows/update.yml`)

1. Checkout repository
2. Install Python 3.11 with pip cache
3. Install dependencies + Playwright Chromium
4. Run `python scraper.py`
5. Check if files changed (partidos.ics, partidos.json, index.html, dashboard.html)
6. Commit & push changes if detected
7. Deploy to `gh-pages` branch (excludes templates, scraper, .github)

### Required GitHub Settings

- **Actions permissions**: Read and write permissions enabled
- **GitHub Pages**: Deploy from `gh-pages` branch, `/ (root)` folder

## Important URLs to Update

When deploying to your own repository, update in `scraper.py` (line 596):

```python
base_url = "https://YOUR-USERNAME.github.io/YOUR-REPO"
```

This affects calendar subscription URLs in generated HTML.

## Updating for New Season

When FFCV releases new season URLs:

1. Update `URL_CALENDARIO` (line 36)
2. Update `URL_CLASIFICACION` (line 37)
3. Update `GRUPO` (line 33) with new group name
4. Commit and push - GitHub Actions will handle the rest

## Common Debugging

### Scraper Fails Locally

1. Verify Playwright installed: `playwright install chromium`
2. Check if FFCV URLs changed (they change each season)
3. Run `debug_scraper.py` to save raw HTML and inspect structure
4. Review logs for specific parsing errors

### HTML Structure Changed

If FFCV redesigns their site:

1. Run `debug_scraper.py` to save current HTML
2. Open `debug_calendario.html` and `debug_partidos.html` in browser
3. Use browser DevTools (F12) to inspect new structure
4. Update CSS selectors in `scrape_calendario()` and `scrape_clasificacion()`
5. Test locally before pushing

### GitHub Actions Permission Errors

1. Go to **Settings** > **Actions** > **General**
2. Set "Workflow permissions" to **Read and write permissions**
3. Enable **Allow GitHub Actions to create and approve pull requests**

## Technical Notes

- **Why Playwright?** FFCV blocks simple HTTP requests; Playwright simulates real browser
- **Retry Logic**: 3 attempts with 5-second delays handle transient network issues
- **Data Preservation**: On error, previous data files remain intact (no overwrite)
- **Rate Limiting**: 2-second delays between requests respect FFCV servers
- **Privacy**: Only public match data is scraped (no player names or personal info)
