#!/usr/bin/env python3
"""parse_cfdi.py <dir> — CFDI XML set -> harvest JSON (kameloso schema).

The CFE CFDI is the source of truth for everything. This module maps the
XML key-value structure to the harvest schema explicitly (see _KV below), so
each field is always read from its canonical element:

  /Comprobante/{Total,Fecha,Moneda,LugarExpedicion}
  /Comprobante/cfdi:Receptor/{Rfc,Nombre}          (Nombre TRUNCATES — see below)
  /Comprobante/Addenda/CFE/ComisionFederalElectricidad/{RPU}
  /Comprobante/Addenda/clsRegArchFact/…            (the full record — see _KV)

The full razón social lives in clsRegArchFact/NOMBRE ("ACTIVOS JAL SA DE CV");
Receptor.Nombre and FIS_RAZON_SOCIAL truncate ("ACTIVOS JAL"). The longest
candidate wins, which is always the full name.

Consumption: CONSUMO_R is the billed kWh (validated against the golden
engine: 48208 == 48208.0). The 15-month history lives in MESR##/AAR##/
CONSUMOR##/DEMANDAR##; the 11 prior totals in Impo_Fact##/Impo_Paga##.

Usage: parse_cfdi.py <dir-with-xmls> [--json-file out.json]
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CFD = "{http://www.sat.gob.mx/cfd/4}"

# Canonical KV map: semantic field -> ordered candidate element paths
# (relative to root). First hit wins. Every value stays a string here.
_KV = {
    "rpu": ["Addenda/clsRegArchFact/RPU",
            "Addenda/CFE/ComisionFederalElectricidad/RPU"],
    "nombre": ["Addenda/clsRegArchFact/NOMBRE",
               "cfdi:Receptor/@Nombre",
               "Addenda/clsRegArchFact/FIS_RAZON_SOCIAL"],
    "rfc": ["Addenda/clsRegArchFact/RFC",
            "cfdi:Receptor/@Rfc"],
    "tarifa": ["Addenda/clsRegArchFact/TARIFA",
               "Addenda/CFE/TARIFA"],
    "tarifa_sicom": ["Addenda/clsRegArchFact/TarifaSICOM"],
    "tarifa_reg": ["Addenda/clsRegArchFact/TARIFA_REG"],
    "periodo_aamm": ["Addenda/clsRegArchFact/OCR_AAMM",
                     "Addenda/CFE/@OCR_AAAAMM"],
    "mes_nombre": ["Addenda/clsRegArchFact/OCR_MM_NOM"],
    "fecha_inicio": ["Addenda/clsRegArchFact/FECDESDE"],
    "fecha_fin": ["Addenda/clsRegArchFact/FECHASTA"],
    "fecha_limite": ["Addenda/clsRegArchFact/FECLIMITE"],
    "fecha_corte": ["Addenda/clsRegArchFact/FECORTE"],
    "total": ["cfdi:Comprobante/@Total"],
    "total_enrol": ["Addenda/clsRegArchFact/IMPTOTAL",       # no decimals — enrollment value
                    "Addenda/clsRegArchFact/TOTAL_SIN_ADE"],
    "subtotal": ["Addenda/clsRegArchFact/SubTotal"],
    "iva": ["Addenda/clsRegArchFact/IMPIVA"],
    "consumo_kwh": ["Addenda/clsRegArchFact/CONSUMO_R"],
    "demanda_kw": ["Addenda/clsRegArchFact/DEMANDA"],
    "kvarh": ["Addenda/clsRegArchFact/KVARH"],
    "consumo_fases": ["Addenda/clsRegArchFact/CONSUMO1F",    # 3 sub-period consumos
                      "Addenda/clsRegArchFact/CONSUMO2F",
                      "Addenda/clsRegArchFact/CONSUMO3F"],
    "cuenta": ["Addenda/clsRegArchFact/NumCta"],             # 82DN20B018200290
    "uuid": ["Addenda/clsRegArchFact/UUID",
             "Complemento/TimbreFiscalDigital/@UUID"],
    "folio": ["Addenda/clsRegArchFact/FE_FOLIO"],
    "fecha_timbrado": ["Addenda/clsRegArchFact/FechaDeTimbrado"],
    "barcode": ["Addenda/clsRegArchFact/CodigoDeBarras"],
    "linea_referencia": ["Addenda/clsRegArchFact/LineaDeReferencia"],
    "giro": ["Addenda/clsRegArchFact/GIROO"],
    "tipo_consumo": ["Addenda/clsRegArchFact/TipoDeConsumo"],
    # service address (NOT the fiscal FIS_* address)
    "dir_calle": ["Addenda/clsRegArchFact/DIRECC", "Addenda/Datos/@calle"],
    "dir_colonia": ["Addenda/clsRegArchFact/COLONIA"],
    "dir_poblacion": ["Addenda/clsRegArchFact/NOMPOB", "Addenda/Datos/@municipio"],
    "dir_estado": ["Addenda/clsRegArchFact/NOMEST", "Addenda/Datos/@estado"],
    "dir_cp": ["Addenda/clsRegArchFact/CODIGO_POSTAL"],
    "contacto": ["Addenda/clsRegArchFact/NOMBREJ"],
    "contacto_tel": ["Addenda/clsRegArchFact/TELEJ"],
    "ciclo": ["Addenda/clsRegArchFact/CICLO"],
    "division": ["Addenda/clsRegArchFact/DIVISION"],
    "zona": ["Addenda/clsRegArchFact/ZONA"],
}


def _iter_text(e: ET.Element, path: str):
    """Yield values for a candidate path (tag or tag/@attr), in document order."""
    segs = path.split("/")
    found = False
    for el in e.iter():
        if el.tag == CFD + segs[-1] or el.tag.rsplit("}", 1)[-1] == segs[-1]:
            yield el.text
            found = True
    if not found:
        return


def _get(e: ET.Element, path: str):
    tag, _, attr = path.rsplit("/", 1)[-1].partition("/@")
    for el in e.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            if attr:
                v = el.attrib.get(attr)
            else:
                v = el.text
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def _historial(root: ET.Element) -> list:
    """15-month history rows: MESR##/AAR##/CONSUMOR##/DEMANDAR## (+11 prior totals)."""
    rows = []
    for i in range(1, 16):
        n = f"{i:02d}"
        mes = _get(root, f"Addenda/clsRegArchFact/MESR{n}")
        if not mes:
            continue
        rows.append({
            "mes": mes,
            "anio": _get(root, f"Addenda/clsRegArchFact/AAR{n}"),
            "consumo_kwh": int(_get(root, f"Addenda/clsRegArchFact/CONSUMOR{n}") or 0),
            "demanda_kw": int(_get(root, f"Addenda/clsRegArchFact/DEMANDAR{n}") or 0),
        })
    prior_totals = []
    for i in range(1, 12):
        v = _get(root, f"Addenda/clsRegArchFact/Impo_Fact{i}")
        if v:
            prior_totals.append(int(v))
    return rows, prior_totals


def parse_one(path: Path) -> dict:
    root = ET.parse(path).getroot()
    rec = _get(root, "cfdi:Receptor/@Nombre")  # may be truncated; longest wins below
    out: dict = {
        "rpu": _get(root, _KV["rpu"][0]) or _get(root, _KV["rpu"][1]),
        "nombre": None,
        "rfc": _get(root, _KV["rfc"][0]) or _get(root, _KV["rfc"][1]),
        "tarifa": _get(root, _KV["tarifa"][0]) or _get(root, _KV["tarifa"][1]),
        "tarifa_sicom": _get(root, _KV["tarifa_sicom"][0]),
        "cuenta": _get(root, _KV["cuenta"][0]),
        "periodo": _get(root, _KV["periodo_aamm"][0]) or _get(root, _KV["periodo_aamm"][1]),
        "mes": _get(root, _KV["mes_nombre"][0]),
        "fecha_inicio": _get(root, _KV["fecha_inicio"][0]),
        "fecha_fin": _get(root, _KV["fecha_fin"][0]),
        "fecha_limite": _get(root, _KV["fecha_limite"][0]),
        "fecha_corte": _get(root, _KV["fecha_corte"][0]),
        "total": float(root.attrib["Total"]) if root.attrib.get("Total") else None,
        "total_enrol": _get(root, _KV["total_enrol"][0]),
        "subtotal": _get(root, _KV["subtotal"][0]),
        "iva": _get(root, _KV["iva"][0]),
        "consumo_kwh": int(_get(root, _KV["consumo_kwh"][0])) if _get(root, _KV["consumo_kwh"][0]) else None,
        "demanda_kw": int(_get(root, _KV["demanda_kw"][0])) if _get(root, _KV["demanda_kw"][0]) else None,
        "kvarh": _get(root, _KV["kvarh"][0]),
        "uuid": _get(root, _KV["uuid"][0]) or _get(root, _KV["uuid"][1]),
        "folio": _get(root, _KV["folio"][0]),
        "fecha": root.attrib.get("Fecha"),
        "barcode": _get(root, _KV["barcode"][0]),
        "giro": _get(root, _KV["giro"][0]),
        "contacto": _get(root, _KV["contacto"][0]),
        "contacto_tel": _get(root, _KV["contacto_tel"][0]),
        "direccion": None,
        "file": str(path),
    }
    # nombre: longest candidate wins (full razón social always longest)
    names = [v for v in (
        _get(root, _KV["nombre"][0]), rec, _get(root, _KV["nombre"][2])
    ) if v]
    out["nombre"] = max(names, key=len) if names else None
    # direccion: service address from clsRegArchFact first
    addr = [_get(root, p) for p in (
        _KV["dir_calle"][0], _KV["dir_colonia"][0],
        _KV["dir_poblacion"][0], _KV["dir_estado"][0], _KV["dir_cp"][0],
    )]
    if addr[0]:
        out["direccion"] = " ".join(v for v in addr if v)
    else:
        out["direccion"] = " ".join(
            v for v in (_get(root, _KV["dir_calle"][1]),
                        _get(root, _KV["dir_poblacion"][1]),
                        _get(root, _KV["dir_estado"][1])) if v
        )
    rows, prior = _historial(root)
    out["historial_meses"] = rows
    out["historial_importes"] = prior
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    d = Path(sys.argv[1])
    xmls = sorted(d.glob("*.xml"))
    if not xmls:
        sys.stderr.write(f"no XMLs in {d}\n")
        return 1
    bills = [parse_one(p) for p in xmls]
    bills.sort(key=lambda b: b.get("fecha") or "")
    latest = bills[-1]
    record = {
        "rpu": latest.get("rpu"),
        "nombre": latest.get("nombre"),
        "rfc": latest.get("rfc"),
        "direccion": latest.get("direccion"),
        "tarifa": latest.get("tarifa"),
        "tarifa_sicom": latest.get("tarifa_sicom"),
        "cuenta": latest.get("cuenta"),
        "periodo": sorted({b.get("periodo") for b in bills if b.get("periodo")}),
        "total": latest.get("total"),
        "total_enrol": latest.get("total_enrol"),
        "consumo_kwh": latest.get("consumo_kwh"),
        "demanda_kw": latest.get("demanda_kw"),
        "historial": [
            {"fecha": b.get("fecha"), "total": b.get("total"),
             "consumo_kwh": b.get("consumo_kwh"), "uuid": b.get("uuid")}
            for b in bills
        ],
        "historial_meses": latest.get("historial_meses"),
        "historial_importes": latest.get("historial_importes"),
        "xml_urls": [b.get("file") for b in bills],
        "pdf_urls": [str(p.with_suffix(".pdf")) for p in xmls],
    }
    out = json.dumps(record, ensure_ascii=False, indent=2)
    print(out)
    if len(sys.argv) > 2 and sys.argv[2] == "--json-file" and len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
