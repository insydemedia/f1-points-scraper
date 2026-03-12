"""
F1 Championship Standings Scraper
==================================
Fetches the current F1 Drivers' Championship standings from formula1.com and
writes f1_championship_standing.json in the format required by Speedcafe.

Strategy:
- During the season:  parse the live standings HTML table (Pos/Driver/Nationality/Team/Pts),
  merge with DRIVER_ROSTER for number, car, poles, wins.
- Pre-season / empty table: output all drivers from DRIVER_ROSTER with zeros.

Column detection is dynamic — the scraper checks header text rather than
fixed indices, so mid-season structural changes are handled gracefully.
"""

import sys
import json
import logging
from collections import Counter
from typing import Optional
import requests
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Configuration ────────────────────────────────────────────────────────────
YEAR = datetime.now().year
STANDINGS_URL = f"https://www.formula1.com/en/results/{YEAR}/drivers"
OUTPUT_FILE = "f1_championship_standing.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── 2026 Driver Roster ───────────────────────────────────────────────────────
# Keyed by (first_name, last_name) for matching against the standings table.
# Includes: driver number, full team name, car model, default order (pre-season).
# Update car names once the 2026 constructors are confirmed.
DRIVER_ROSTER = [
    # Alpine
    {"first": "Pierre",     "last": "Gasly",      "number": 10, "team": "Alpine",          "car": "Alpine A526"},
    {"first": "Franco",     "last": "Colapinto",  "number": 43, "team": "Alpine",          "car": "Alpine A526"},
    # Aston Martin
    {"first": "Fernando",   "last": "Alonso",     "number": 14, "team": "Aston Martin",    "car": "Aston Martin AMR26"},
    {"first": "Lance",      "last": "Stroll",     "number": 18, "team": "Aston Martin",    "car": "Aston Martin AMR26"},
    # Audi
    {"first": "Nico",       "last": "Hulkenberg", "number": 27, "team": "Audi",            "car": "Audi R26"},
    {"first": "Gabriel",    "last": "Bortoleto",  "number": 5,  "team": "Audi",            "car": "Audi R26"},
    # Cadillac (car name TBC — not yet announced)
    {"first": "Sergio",     "last": "Perez",      "number": 11, "team": "Cadillac",        "car": "Cadillac"},
    {"first": "Valtteri",   "last": "Bottas",     "number": 77, "team": "Cadillac",        "car": "Cadillac"},
    # Ferrari
    {"first": "Charles",    "last": "Leclerc",    "number": 16, "team": "Ferrari",         "car": "Ferrari SF-26"},
    {"first": "Lewis",      "last": "Hamilton",   "number": 44, "team": "Ferrari",         "car": "Ferrari SF-26"},
    # Haas
    {"first": "Esteban",    "last": "Ocon",       "number": 31, "team": "Haas F1 Team",    "car": "Haas VF-26"},
    {"first": "Oliver",     "last": "Bearman",    "number": 87, "team": "Haas F1 Team",    "car": "Haas VF-26"},
    # McLaren
    {"first": "Lando",      "last": "Norris",     "number": 1,  "team": "McLaren",         "car": "McLaren MCL40"},
    {"first": "Oscar",      "last": "Piastri",    "number": 81, "team": "McLaren",         "car": "McLaren MCL40"},
    # Mercedes
    {"first": "George",     "last": "Russell",    "number": 63, "team": "Mercedes",        "car": "Mercedes W17"},
    {"first": "Kimi",       "last": "Antonelli",  "number": 12, "team": "Mercedes",        "car": "Mercedes W17"},
    # Racing Bulls
    {"first": "Liam",       "last": "Lawson",     "number": 30, "team": "Racing Bulls",    "car": "Racing Bulls VCARB 03"},
    {"first": "Arvid",      "last": "Lindblad",   "number": 6,  "team": "Racing Bulls",    "car": "Racing Bulls VCARB 03"},
    # Red Bull Racing
    {"first": "Max",        "last": "Verstappen", "number": 3,  "team": "Red Bull Racing", "car": "Red Bull RB22"},
    {"first": "Isack",      "last": "Hadjar",     "number": 22, "team": "Red Bull Racing", "car": "Red Bull RB22"},
    # Williams
    {"first": "Carlos",     "last": "Sainz",      "number": 55, "team": "Williams",        "car": "Williams FW48"},
    {"first": "Alexander",  "last": "Albon",      "number": 23, "team": "Williams",        "car": "Williams FW48"},
]

# Build lookup dict keyed by lowercased full name for flexible matching
_ROSTER_BY_NAME = {
    f"{d['first'].lower()} {d['last'].lower()}": d for d in DRIVER_ROSTER
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clean_int(text: str) -> int:
    """Strip everything except digits and return int, or 0."""
    digits = "".join(c for c in (text or "") if c.isdigit())
    return int(digits) if digits else 0


def lookup_driver(first: str, last: str) -> Optional[dict]:
    """Return roster entry for a driver name (case-insensitive)."""
    key = f"{first.strip().lower()} {last.strip().lower()}"
    return _ROSTER_BY_NAME.get(key)


def detect_columns(header_row) -> dict:
    """
    Return a dict mapping semantic column name → 0-based index.
    Handles any ordering or additional/missing columns gracefully.
    """
    cols = {}
    for i, th in enumerate(header_row.find_all("th")):
        text = th.get_text(strip=True).lower()
        if "pos" in text:
            cols["pos"] = i
        elif "driver" in text:
            cols["driver"] = i
        elif "nation" in text:
            cols["nationality"] = i
        elif "team" in text or "constructor" in text:
            cols["team"] = i
        elif "pts" in text or "point" in text:
            cols["pts"] = i
    return cols


# ─── Scraping ─────────────────────────────────────────────────────────────────

def fetch_wins() -> Counter:
    """Fetch race winners and return a Counter of wins per driver."""
    url = f"https://www.formula1.com/en/results/{YEAR}/races"
    wins = Counter()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table")
        if table:
            headers = [th.get_text(strip=True).lower() for th in table.find('thead').find_all('th')]
            winner_idx = headers.index('winner') if 'winner' in headers else -1
            if winner_idx != -1:
                # Get wins row by row
                for row in table.find("tbody").find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) > winner_idx:
                        # e.g "Max Verstappen VER"
                        full_text = cells[winner_idx].get_text(separator=" ", strip=True)
                        parts = full_text.split()
                        # Remove the 3-letter abbreviation suffix if present
                        parts = [p for p in parts if not (p.isupper() and len(p) == 3)]
                        winner_name = " ".join(parts).strip()
                        if winner_name:
                            wins[winner_name] += 1
        return wins
    except Exception as e:
        logging.error(f"Error fetching wins from {url}: {e}")
        return Counter()


def fetch_poles() -> Counter:
    """Fetch pole positions and return a Counter of poles per driver."""
    url = f"https://www.formula1.com/en/results/{YEAR}/awards/pole-positions"
    poles = Counter()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table")
        if table:
            headers = [th.get_text(strip=True).lower() for th in table.find('thead').find_all('th')]
            driver_idx = headers.index('winner') if 'winner' in headers else -1
            if driver_idx != -1:
                for row in table.find("tbody").find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) > driver_idx:
                        # e.g "Max Verstappen VER"
                        full_text = cells[driver_idx].get_text(separator=" ", strip=True)
                        parts = full_text.split()
                        parts = [p for p in parts if not (p.isupper() and len(p) == 3)]
                        driver_name = " ".join(parts).strip()
                        if driver_name:
                            poles[driver_name] += 1
        return poles
    except Exception as e:
        logging.error(f"Error fetching poles from {url}: {e}")
        return Counter()


def fetch_standings() -> Optional[list]:
    """
    Fetch the live F1 standings page and parse the HTML table.

    Returns:
        List of driver dicts, or None if the page/table could not be fetched.
        Returns an empty list if the table exists but has no result rows.
    """
    logging.info(f"Fetching standings from {STANDINGS_URL}")
    try:
        resp = requests.get(STANDINGS_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"HTTP error fetching standings page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # The page contains exactly one <table>; using find() rather than a fixed
    # class name makes the scraper resilient to CSS-module class renames.
    table = soup.find("table")
    if not table:
        logging.error("No <table> found on the standings page.")
        return None

    thead = table.find("thead")
    tbody = table.find("tbody")
    if not thead or not tbody:
        logging.error("Table is missing <thead> or <tbody>.")
        return None

    header_row = thead.find("tr")
    if not header_row:
        logging.error("No header row in <thead>.")
        return None

    cols = detect_columns(header_row)
    logging.info(f"Detected columns: {cols}")

    # Minimum required columns for standings
    required = {"pos", "driver", "pts"}
    missing = required - cols.keys()
    if missing:
        logging.error(f"Required columns not found in standings table: {missing}")
        return None

    wins_counts = fetch_wins()
    poles_counts = fetch_poles()

    standings = []
    for row in tbody.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        # Skip "no results" rows (have a single colspan cell)
        if len(cells) == 1:
            logging.info("Table is empty — no race results yet this season.")
            return []

        def cell_text(col_name: str) -> str:
            idx = cols.get(col_name)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].get_text(separator=" ", strip=True)

        # ── Position ──────────────────────────────────────────────────────────
        place = clean_int(cell_text("pos"))

        # ── Driver name ───────────────────────────────────────────────────────
        # The driver cell contains two <span> or <p> elements for first/last name
        driver_cell_idx = cols.get("driver", 0)
        if driver_cell_idx < len(cells):
            driver_cell = cells[driver_cell_idx]
            all_text = driver_cell.get_text(separator=" ", strip=True)
            parts = all_text.split()
            parts = [p for p in parts if not (p.isupper() and len(p) == 3)]
            first_name = parts[0] if parts else ""
            last_name  = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            first_name, last_name = "", ""

        full_name = f"{first_name} {last_name}".strip()

        # ── Merge with roster ─────────────────────────────────────────────────
        roster_entry = lookup_driver(first_name, last_name)
        if roster_entry is None:
            logging.warning(f"Driver '{full_name}' not found in DRIVER_ROSTER — skipping.")
            continue

        # ── Points, wins, poles ───────────────────────────────────────────────
        points = clean_int(cell_text("pts"))
        
        # Matches by exact full name string built above
        wins = wins_counts.get(full_name, 0)
        poles = poles_counts.get(full_name, 0)

        standings.append({
            "place":  place,
            "number": roster_entry["number"],
            "team":   roster_entry["team"],
            "name":   full_name,
            "car":    roster_entry["car"],
            "poles":  poles,
            "wins":   wins,
            "points": points,
            "odds":   {"bet365": "0", "sportsbet": "0", "dabble": "0"},
        })

    logging.info(f"Parsed {len(standings)} drivers from live standings table.")
    return standings


def build_preseason_standings() -> list[dict]:
    """
    Build a zeroed-out standings list from the hardcoded DRIVER_ROSTER.
    Used when the season hasn't started yet and the table is empty.
    """
    logging.info("Building pre-season standings from DRIVER_ROSTER (all zeros).")
    return [
        {
            "place":  i + 1,
            "number": d["number"],
            "team":   d["team"],
            "name":   f"{d['first']} {d['last']}",
            "car":    d["car"],
            "poles":  0,
            "wins":   0,
            "points": 0,
            "odds":   {"bet365": "0", "sportsbet": "0", "dabble": "0"},
        }
        for i, d in enumerate(DRIVER_ROSTER)
    ]


# ─── Output ───────────────────────────────────────────────────────────────────

def save_json(data: list[dict]) -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logging.info(f"Saved {len(data)} entries to {OUTPUT_FILE}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    standings = fetch_standings()

    if standings is None:
        logging.error("Could not fetch standings page. Aborting.")
        sys.exit(1)

    if len(standings) == 0:
        # Pre-season or bye week — use zeroed roster
        standings = build_preseason_standings()

    save_json(standings)
    logging.info("Done.")


if __name__ == "__main__":
    main()
