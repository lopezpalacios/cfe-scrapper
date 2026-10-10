"""Manifest of monthly CFE tariff PDFs.

Each entry points to one monthly 'Tarifas finales del Suministro Básico' PDF.
Two hosts serve the same documents; DOF is WAF-free (primary), CENACE is the
filename-pattern fallback. The CFE app portal (app.cfe.mx) is WAF-blocked and is
NOT used.

Auto-discovery of new months (the DOF publication-date filename, e.g.
CFE_280122.pdf, is not derivable from the tariff month) is a TODO — for now the
manifest is curated/appended as each month is published. `discover()` is stubbed
with the intended strategy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "manifest.json")


@dataclass
class Entry:
    year: int
    month: int
    url: str
    host: str           # "dof" | "cenace"
    authority: str      # "CFE" | "CRE" | "CNE" | "SHCP"


# Curated entries — HTTP 200 AND parse-verified (17 divisiones, 1810 cells each)
# on 2026-06-21. Only the modern table format (≈2024→present) parses with the
# current parser; the ≤2023 "Tarifas de Suministro Básico" layout differs and
# needs a parser variant (tracked — see discover() notes).
SEED: list[Entry] = [
    Entry(2025, 9, "https://www.cenace.gob.mx/Docs/16_MARCOREGULATORIO/Suministro/"
          "(DOF%202025-09-22%20CFE)%20Tarifas%20finales%20Suministro%20B%C3%A1sico"
          "%20vigente%20desde%201%20septiembre%202025.pdf", "cenace", "CFE"),
    Entry(2024, 12, "https://www.cenace.gob.mx/Docs/16_MARCOREGULATORIO/Suministro/"
          "(DOF%202025-01-09%20CRE)%20Acuerdo%20de%20tarifas%20finales%20del%20"
          "suministro%20b%C3%A1sico%20de%20diciembre%202024.pdf", "cenace", "CRE"),
]


def load() -> list[Entry]:
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as fh:
            return [Entry(**e) for e in json.load(fh)]
    return list(SEED)


def save(entries: list[Entry]) -> None:
    with open(MANIFEST_PATH, "w") as fh:
        json.dump([asdict(e) for e in entries], fh, indent=2, ensure_ascii=False)


def ensure_seed() -> list[Entry]:
    """Write the seed manifest if none exists; return current entries."""
    if not os.path.exists(MANIFEST_PATH):
        save(SEED)
    return load()


def discover() -> list[Entry]:
    """Auto-discovery of monthly PDF URLs — harvest, don't construct.

    Verified facts (2026-06-21) that shape this:
      * app.cfe.mx is WAF-blocked (403). Dead.
      * DOF `CFE_<DDMMYY>.pdf` uses the publication date, which is NOT the tariff
        month and NOT derivable; some months have no DOF /CFE/ file at all.
        (e.g. CFE_120522.pdf 404s.) So DOF filenames cannot be constructed.
      * CENACE `Docs/16_MARCOREGULATORIO/Suministro/` is the canonical archive,
        but its directory index returns 403 and filenames are inconsistent across
        eras: "(DOF YYYY-MM-DD AUTH) Tarifas finales ... vigente desde 1 <mes>"
        vs "... Acuerdo de tarifas finales del suministro básico de <mes> <año>"
        vs (≤2023) "Tarifas de Suministro Básico (a partir de <mes> <año>)".
      * The ≤2023 layout does NOT parse with the current parser (0 rows) — a
        parser variant is required before those months are usable.

    Working method: search-engine harvest, then verify each candidate.
      1. WebSearch `site:cenace.gob.mx 16_MARCOREGULATORIO Suministro "Tarifas
         finales" <year>` (and the "Acuerdo de tarifas finales" wording) per year.
      2. For each URL: GET with a browser UA, require %PDF magic, then run
         parse.py and keep ONLY URLs that yield 17 divisiones / ~1810 cells.
      3. Append verified Entry(...) rows to the manifest.

    This is a curated harvest (engine-dependent), so it is run as a maintained
    batch rather than a fully-blind constructor. Until automated, append verified
    months to SEED / manifest.json manually.
    """
    raise NotImplementedError(
        "harvest via WebSearch + parse-verify; see docstring. Append verified "
        "months to the manifest as a maintained batch.")


if __name__ == "__main__":
    entries = ensure_seed()
    for e in entries:
        print(f"{e.year}-{e.month:02d}  {e.host:6}  {e.authority:4}  {e.url}")
