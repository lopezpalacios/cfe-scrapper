"""CFE división: authoritative resolution from the CFDI bill, not a CP guess.

CFE's own división identifier is ON the bill — no geolocation lookup needed:
  - CFDI addenda field `DIVISION` = 2-letter code (e.g. "DK")
  - `NumCta` prefix encodes it: `82<DIV><ZONA><AGENCIA>...`  e.g. 82 DK 04 G ...
  - plus `ZONA`, `REGION`, `AGENCIA`

This is ground truth. The CP→división lookup (`cfe.cp_lookup` / `ref.cp_division`)
is only a FALLBACK when the code is missing — it has real errors on state-border
CPs (e.g. Saltillo/Coahuila mislabeled Bajío instead of Golfo Norte) and on the
intra-CDMX VM Norte/Centro/Sur split. The bill code corrects those.

CODE→NAME map below is EMPIRICAL: resolved as the modal división over the true
service CP (the RMU 5-digit prefix, NOT `LugarExpedicion` which is the expedition
office) across the Newman warehouse. All 14 names verified against CFE's official
16-división catalog in the Estatuto Orgánico de CFE Distribución (DOF 04/01/2018,
nota 5510072). Two prior ambiguities resolved there: (1) DA=Baja California is a
single división covering BOTH BC and BCS — the "Baja California" división's Sup.
Zona La Paz/Constitución have jurisdiction over BCS, so there is NO separate BCS
código; the BC/BCS mix under DA is correct, not an error. (2) DN=Valle de México
Sur is official división XIV, distinct from Centro Sur (=DG, división V).
(The Estatuto lists the 16 division NAMES, not the 2-letter códigos; the
code→name binding is empirical from the CFDI NumCta, and is consistent with it.)
"""
from __future__ import annotations

# code -> (division_name, confidence)
DIVISION_CODES = {
    "DA": ("Baja California", "high"),  # incl. BCS: no separate BCS division (Estatuto Orgánico CFE Distribución, DOF 04/01/2018): Div. Baja California's Sup. Zona La Paz/Constitución cover BCS
    "DB": ("Noroeste", "high"),
    "DD": ("Golfo Norte", "high"),      # corrects CP-lookup Saltillo->Bajío error
    "DF": ("Centro Occidente", "high"),
    "DG": ("Centro Sur", "high"),
    "DJ": ("Oriente", "high"),
    "DK": ("Sureste", "high"),
    "DL": ("Valle de México Norte", "high"),
    "DM": ("Valle de México Centro", "med"),   # some VM-Norte boundary bleed
    "DN": ("Valle de México Sur", "high"),      # official div. XIV per Estatuto (DOF 04/01/2018); distinct from Centro Sur (DG=div. V)
    "DP": ("Bajío", "high"),
    "DV": ("Centro Oriente", "high"),
    "DW": ("Peninsular", "high"),
    "DX": ("Jalisco", "high"),
}


def division_from_numcta(numcta: str) -> str | None:
    """`NumCta` -> 2-letter división code. e.g. '82DK04G010010520' -> 'DK'."""
    if not numcta or len(numcta) < 4:
        return None
    return numcta[2:4].upper()


def resolve_division(division_code: str | None = None,
                     numcta: str | None = None) -> dict:
    """Bill fields -> {division, code, confidence, source}. Ground truth path.

    Prefer the explicit CFDI `DIVISION` code; else derive from `NumCta`. Returns
    source='cfdi_code'. Caller falls back to CP lookup only if this returns None.
    """
    code = (division_code or "").strip().upper() or division_from_numcta(numcta or "")
    if not code:
        return {"division": None, "code": None, "confidence": None,
                "source": None}
    name, conf = DIVISION_CODES.get(code, (None, None))
    return {"division": name, "code": code, "confidence": conf,
            "source": "cfdi_code" if name else "cfdi_code_unmapped"}


if __name__ == "__main__":
    import sys
    print(resolve_division(numcta=sys.argv[1] if len(sys.argv) > 1
                           else "82DK04G010010520"))
