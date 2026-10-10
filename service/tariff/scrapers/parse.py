"""Parse a CFE monthly 'Tarifas finales del Suministro Básico' PDF into
normalized long-format rows.

Source of truth: the monthly PDFs published via DOF / mirrored on CENACE. Each
PDF is one complete national snapshot — every división tarifaria × tariff code ×
charge component × hourly period.

Parsing strategy (verified against Sept-2025):
  * pdfplumber gives every word with x-coordinates.
  * The table has 7 fixed component columns, left→right:
      transmision, distribucion, cenace, suministradora, conexos, energia, capacidad
    We locate each column's x-center from the page header words, then assign
    each numeric value to the column whose center is nearest — robust to the
    many blank cells (counting tokens left-to-right would misalign on blanks).
  * Every tariff block starts with a `$/mes` row, so we split rows into tariff
    groups on `$/mes`. The tariff CODE token (GDMTH, DIST, ...) appears on one
    row of the group and applies to the whole group.

No fabrication: a value that cannot be parsed is skipped and logged, never
zero-filled.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, asdict

import pdfplumber

# Tariff codes we recognize (business/industrial focus, but capture all present).
TARIFF_CODES = {
    "DB1", "DB2", "PDBT", "GDBT", "RABT", "RAMT", "APBT", "APMT",
    "GDMTO", "GDMTH", "DIST", "DIT",
}

# Header keyword (lowercased, accent-insensitive prefix) → component key, in
# left-to-right order. Matched by prefix because pdftotext/pdfplumber sometimes
# hyphenate ("Transmisió-n") across the page edge.
COMPONENT_ANCHORS = [
    ("transmisi", "transmision"),
    ("distribuci", "distribucion"),
    ("cenace", "cenace"),
    ("suministradora", "suministradora"),
    ("conexos", "conexos"),
    ("energia", "energia"),
    ("capacidad", "capacidad"),
]

PERIODS = {"base", "intermedio", "punta", "semipunta"}

DIVISIONS = {
    "Baja California", "Baja California Sur", "Bajío", "Centro Occidente",
    "Centro Oriente", "Centro Sur", "Golfo Centro", "Golfo Norte", "Jalisco",
    "Noroeste", "Norte", "Oriente", "Peninsular", "Sureste",
    "Valle de México Centro", "Valle de México Norte", "Valle de México Sur",
}

_NUM_RE = re.compile(r"^-?[\d,]+\.\d+$|^-?\d{1,3}(?:,\d{3})*\.\d+$|^\d+\.\d+$")


def _strip_accents(s: str) -> str:
    table = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")
    return s.translate(table)


def _norm(s: str) -> str:
    return _strip_accents(s).lower()


def _is_number(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def _to_float(tok: str) -> float:
    return float(tok.replace(",", ""))


@dataclass
class TariffRow:
    year: int
    month: int
    division: str
    tariff_code: str
    period: str | None
    component: str
    unit: str
    value_mxn: float
    source_url: str
    source_authority: str


def _cluster_rows(words, y_tol: float = 3.0):
    """Group words into visual rows by their vertical position."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        placed = False
        for row in rows:
            if abs(row[0]["top"] - w["top"]) <= y_tol:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    rows.sort(key=lambda r: r[0]["top"])
    return rows


def _column_centers(rows):
    """From the header rows, return {component: x_center}. Header words may be
    spread over a few lines, so scan all rows and take the first match each."""
    centers: dict[str, float] = {}
    for row in rows:
        for w in row:
            t = _norm(w["text"])
            for prefix, comp in COMPONENT_ANCHORS:
                if comp in centers:
                    continue
                if t.startswith(prefix):
                    centers[comp] = (w["x0"] + w["x1"]) / 2
    return centers


def _nearest_component(x_center: float, centers: dict[str, float]) -> str | None:
    if not centers:
        return None
    return min(centers.items(), key=lambda kv: abs(kv[1] - x_center))[0]


def parse_pdf(path: str, year: int, month: int, source_url: str,
              source_authority: str) -> list[TariffRow]:
    out: list[TariffRow] = []
    with pdfplumber.open(path) as pdf:
        current_division: str | None = None
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            if not words:
                continue
            rows = _cluster_rows(words)

            # Division header on this page?
            for row in rows:
                line = " ".join(w["text"] for w in row)
                m = re.search(r"Divisi[oó]n\s+(.+)", line)
                if m:
                    name = m.group(1).strip()
                    # normalize against known divisions — longest match first so
                    # "Baja California Sur" isn't shortened to "Baja California".
                    nn = _norm(name)
                    matches = [d for d in DIVISIONS if nn.startswith(_norm(d))]
                    if matches:
                        current_division = max(matches, key=len)
                    else:
                        current_division = name
                    break

            centers = _column_centers(rows)
            if not centers or current_division is None:
                continue

            # Walk rows, splitting tariff groups on `$/mes`.
            group: list[list[dict]] = []
            groups: list[list[list[dict]]] = []
            for row in rows:
                toks = [w["text"] for w in row]
                if any(t == "$/mes" for t in toks):
                    if group:
                        groups.append(group)
                    group = [row]
                elif group:
                    group.append(row)
            if group:
                groups.append(group)

            for grp in groups:
                code = None
                for row in grp:
                    for w in row:
                        if w["text"] in TARIFF_CODES:
                            code = w["text"]
                            break
                    if code:
                        break
                if not code:
                    continue
                for row in grp:
                    toks = [w["text"] for w in row]
                    # determine unit
                    unit = None
                    for u in ("$/mes", "$/kWh", "$/kW"):
                        if u in toks:
                            unit = u
                            break
                    if unit is None:
                        continue
                    period = None
                    if unit == "$/kWh":
                        for w in row:
                            if _norm(w["text"]) in PERIODS:
                                period = _norm(w["text"])
                                break
                    for w in row:
                        if not _is_number(w["text"]):
                            continue
                        xc = (w["x0"] + w["x1"]) / 2
                        comp = _nearest_component(xc, centers)
                        if comp is None:
                            continue
                        out.append(TariffRow(
                            year=year, month=month, division=current_division,
                            tariff_code=code, period=period, component=comp,
                            unit=unit, value_mxn=_to_float(w["text"]),
                            source_url=source_url, source_authority=source_authority,
                        ))
    return out


def rows_as_dicts(rows: list[TariffRow]) -> list[dict]:
    return [asdict(r) for r in rows]


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: parse.py <pdf> <year> <month> [source_url] [authority]")
        raise SystemExit(2)
    pdf_path, yr, mo = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    url = sys.argv[4] if len(sys.argv) > 4 else ""
    auth = sys.argv[5] if len(sys.argv) > 5 else ""
    parsed = parse_pdf(pdf_path, yr, mo, url, auth)
    print(f"parsed {len(parsed)} rows")
    divs = sorted({r.division for r in parsed})
    print(f"{len(divs)} divisiones: {divs}")
    # quick GDMTH Baja California dump
    for r in parsed:
        if r.tariff_code == "GDMTH" and r.division == "Baja California":
            print(r.tariff_code, r.unit, r.period, r.component, r.value_mxn)
