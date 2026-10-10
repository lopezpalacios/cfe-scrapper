#!/usr/bin/env python3
"""discover.py — automate new tariff-month discovery for the manifest.

Method (per the upstream docstring, now automated):
  1. DuckDuckGo HTML search per year for the CENACE Suministro archive.
  2. Candidate = .pdf URL under cenace.gob.mx/Docs/16_MARCOREGULATORIO/Suministro/.
  3. GET with a browser UA; keep only %PDF magic files.
  4. parse-verify with the newman-backend parser: >= 1000 rows = verified.
  5. Append verified Entry(year, month, url, host, authority) to manifest.json.

Usage: discover.py [year...]   (defaults: current year and the year before)
Exit 0 = at least one month verified/appended or nothing new.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import date

BACKEND = "/Users/newman/newman-agents/newman-backend/scrapers"
sys.path.insert(0, BACKEND)
from manifest import Entry, load, save  # noqa: E402
from parse import parse_pdf  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
VERIFY_MIN_ROWS = 1000

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

ARCHIVE_PREFIX = "cenace.gob.mx/Docs/16_MARCOREGULATORIO/Suministro/"


def ddg_search(query: str) -> list[str]:
    url = ("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'href="([^"]*uddg=([^"&]+)[^"]*)"', page):
        out.append(urllib.parse.unquote(m.group(2)))
    return out


def candidates_for(year: int) -> list[str]:
    queries = [
        f'site:cenace.gob.mx 16_MARCOREGULATORIO Suministro "Tarifas finales" {year}',
        f'site:cenace.gob.mx 16_MARCOREGULATORIO Suministro "suministro b\u00e1sico" {year}',
        f'site:cenace.gob.mx Suministro "vigente desde" {year}',
        f'site:cenace.gob.mx Suministro "a partir del 1 de" {year}',
    ]
    seen, out = set(), []
    for q in queries:
        for u in ddg_search(q):
            if ARCHIVE_PREFIX in u and u.lower().endswith(".pdf") and u not in seen:
                seen.add(u)
                out.append(u)
    return out


def verify(url: str, year: int, month: int, authority: str) -> int | None:
    """Returns the parsed row count if the PDF verifies, else None.

    The download + parse runs ON newman-ubuntu — the only host with the MX
    residential egress CENACE/DOF accept. This host only orchestrates.
    """
    import shlex
    import subprocess
    remote = (f"/root/tariff-venv/bin/python /root/tariff/fetch_parse.py "
              f"{shlex.quote(url)} {year} {month} {shlex.quote(authority)}")
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", "newman-ubuntu", remote],
        capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        return None
    try:
        rows = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    return len(rows) if rows else 0


def verify_url(url: str) -> tuple[int, int, str] | None:
    """Filename-driven verify: parse month/year/authority from the URL, then
    parse-verify the PDF. Returns (year, month, authority) on success."""
    text = urllib.parse.unquote(url).lower()
    m = re.search(
        r"(?:desde|a partir del|a partir de)\s+(?:el\s+)?(?:0?1|1)\s+(?:de\s+)?(\w+)(?:\s+de)?\s+(\d{4})", text)
    if not m or m.group(1) not in MESES:
        return None
    month, year = MESES[m.group(1)], int(m.group(2))
    a = re.search(r"\(dof[^)]*?\b(cfe|cre|cne|shcp)\b", text)
    auth = a.group(1).upper() if a else "CFE"
    n = verify(url, year, month, auth)
    if n is None or n < VERIFY_MIN_ROWS:
        print(f"  reject ({n} rows): {url[:100]}")
        return None
    print(f"  VERIFIED {year}-{month:02d} {auth} rows={n}  {url[:90]}")
    return year, month, auth


def main() -> int:
    urls = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--url":
            urls.append(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    entries = load()
    existing = {(e.year, e.month) for e in entries}
    verified_new = 0
    for u in urls:
        got = verify_url(u)
        if got is None:
            continue
        year, month, auth = got
        if (year, month) in existing:
            print(f"  already in manifest: {year}-{month:02d}")
            continue
        entries.append(Entry(year, month, u, "cenace", auth))
        existing.add((year, month))
        verified_new += 1

    if verified_new:
        save(entries)
        print(f"manifest updated: {len(entries)} entries (+{verified_new})")
    else:
        print("nothing new verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
