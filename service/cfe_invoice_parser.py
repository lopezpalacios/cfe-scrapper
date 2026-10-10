"""Lossless-source CFE CFDI/Addenda parser for the ``cfe`` warehouse.

This module deliberately does not write to the database.  It turns one XML
document into a deterministic, database-ready payload for ``cfe.invoice_*``.
The original XML remains in ``raw_cfe.*``; the payload contains normalized
facts plus path evidence, not a second copy of the XML.

Important invariants:

* ``TARIFA_REG`` is authoritative.  A missing value is ``None`` and creates a
  quality issue; it is never replaced with GDMTH.
* Missing, explicit zero and invalid numeric values remain distinguishable.
* All repeated CFE Addenda fields are enumerated.  A local-name collision does
  not silently overwrite an earlier value.
* Output is stable and hashable, so retries are idempotent.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


PARSER_VERSION = "cfe-xml-v2.1.0"

TARIFA_ALIAS = {
    "HM": "GDMTH",
    "OM": "GDMTO",
    "HS": "DIST",
    "HSL": "DIST",
    "HT": "DIT",
    "HTL": "DIT",
}

DIVISION_BY_STATE = {
    "BCN": "Baja California",
    "BC": "Baja California",
    "BCS": "Baja California Sur",
    "SON": "Noroeste",
    "SIN": "Noroeste",
    "CHIH": "Norte",
    "DGO": "Norte",
    "COAH": "Norte",
    "NL": "Golfo Norte",
    "TAMPS": "Golfo Norte",
    "JAL": "Jalisco",
    "NAY": "Jalisco",
    "GTO": "Bajío",
    "AGS": "Bajío",
    "QRO": "Bajío",
    "SLP": "Bajío",
    "ZAC": "Bajío",
    "MICH": "Centro Occidente",
    "COL": "Centro Occidente",
    "VER": "Golfo Centro",
    "PUE": "Centro Oriente",
    "TLAX": "Centro Oriente",
    "HGO": "Centro Oriente",
    "MOR": "Centro Sur",
    "GRO": "Centro Sur",
    "OAX": "Oriente",
    "CHIS": "Oriente",
    "YUC": "Peninsular",
    "QROO": "Peninsular",
    "QR": "Peninsular",
    "CAMP": "Peninsular",
    "TAB": "Sureste",
    "MEX": "Valle de México Norte",
    "CDMX": "Valle de México Centro",
    "DF": "Valle de México Centro",
}

MOTIVO_COMPONENT = {
    "ES": "ES",
    "ED": "ED",
    "ET": "ET",
    "EC": "EC",
    "EM": "EM",
}

MONTHS_ES = {
    "ENE": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str | None:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    """Parse a CFE number without converting missing/invalid values to zero."""
    text = _clean(value)
    if text is None:
        return None
    text = text.replace(",", "").replace("$", "").replace("−", "-")
    if not re.search(r"\d", text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return float(number)


def _numeric_quality(raw: Any, value: float | None) -> str:
    text = _clean(raw)
    if text is None:
        return "missing"
    if value is None:
        return "invalid"
    if value == 0:
        return "explicit_zero"
    return "observed"


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3})\s+(\d{2,4})", text.upper())
    if match:
        month = MONTHS_ES.get(match.group(2))
        year = int(match.group(3))
        if year < 100:
            year += 2000
        if month:
            try:
                return date(year, month, int(match.group(1)))
            except ValueError:
                return None
    return None


def _division(state: str | None, municipio: str | None) -> str | None:
    if not state:
        return None
    key = state.strip().upper().rstrip(".")
    result = DIVISION_BY_STATE.get(key)
    if result == "Valle de México Norte" and municipio:
        town = municipio.upper()
        if any(token in town for token in ("ECATEPEC", "TLALNEPANTLA", "NAUCALPAN", "CUAUTITLAN")):
            return "Valle de México Norte"
    return result


def _semantic_line(concept: str) -> str:
    folded = unicodedata.normalize("NFKD", concept.upper())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    if "IVA" in folded:
        return "iva"
    if "DAP" in folded or "DERECHO DE ALUMBRADO" in folded:
        return "dap"
    if "ADEUDO" in folded or "SALDO ANTERIOR" in folded:
        return "adeudo"
    if "PAGO" in folded or "ABONO" in folded:
        return "pago"
    if "SUBTOTAL" in folded:
        return "subtotal"
    if ("FACTURACION DEL PERIODO" in folded or "FACTURACION PERIODO" in folded
            or ("FACTUR" in folded and "PERIODO" in folded)):
        return "facturacion_periodo"
    if re.search(r"\bTOTAL\b", folded):
        return "total"
    if "BONIF" in folded and ("FACTOR" in folded or re.search(r"\bFP\b", folded)):
        return "bonif_fp"
    if ("PENAL" in folded or "CARGO" in folded) and ("FACTOR" in folded or re.search(r"\bFP\b", folded)):
        return "cargo_fp_penalty"
    if "2%" in folded or "BAJA TENSION" in folded:
        return "baja_tension_2pct"
    if "CARGO FIJO" in folded or "SUMINISTRADORA" in folded:
        return "cargo_fijo"
    if "ENERG" in folded or "KWH" in folded:
        return "energia"
    if folded:
        return "other"
    return "other"


def _motivo(code: str) -> tuple[str, str | None]:
    """Return (component, period) while preserving new CFE codes."""
    normalized = code.strip().upper()
    prefix = normalized[:2]
    component = MOTIVO_COMPONENT.get(prefix)
    if normalized.startswith("EG"):
        component = "EG"
        suffix = normalized[2:]
        period = {"B": "B", "I": "I", "P": "P", "S": "S", "1": "FLAT"}.get(suffix)
        return component, period
    if normalized.startswith("EI"):
        return "EI", None  # includes EID and EIK capacity variants
    if normalized.startswith("ES"):
        return "ES", None
    if normalized.startswith("ED"):
        return "ED", None
    if normalized.startswith("ET"):
        return "ET", None
    if normalized.startswith("EC"):
        return "EC", None
    if normalized.startswith("EM"):
        return "EM", None
    return component or "OTHER", None


def _first(values: dict[str, list[str]], name: str) -> str | None:
    for value in values.get(name, []):
        if _clean(value) is not None:
            return _clean(value)
    return None


def _exact(values: dict[str, list[str]], name: str) -> str | None:
    return _first(values, name)


def _numbered(values: dict[str, list[str]], attrs: dict[str, list[str]], number: int, *names: str) -> str | None:
    for name in names:
        result = _exact(values, name.format(number=number)) or _exact(attrs, name.format(number=number))
        if result is not None:
            return result
    return None


def _walk(root: ET.Element) -> tuple[dict[str, list[str]], dict[str, list[str]], list[dict[str, Any]], set[str], int, int]:
    """Collect all element/attribute values and namespace-aware evidence."""
    values: dict[str, list[str]] = defaultdict(list)
    attrs: dict[str, list[str]] = defaultdict(list)
    field_catalog: dict[str, dict[str, Any]] = {}
    observed_paths: set[str] = set()
    nodes = 0
    attribute_count = 0

    def visit(element: ET.Element, parent_path: str) -> None:
        nonlocal nodes, attribute_count
        nodes += 1
        local = _local_name(element.tag)
        namespace = _namespace(element.tag)
        path = f"{parent_path}/{local}" if parent_path else f"/{local}"
        text = _clean(element.text)
        if text is not None:
            values[local].append(text)
            observed_paths.add(path)
            entry = field_catalog.setdefault(path, {"field_path": path, "namespace_uri": namespace, "local_name": local, "value_kind": "text", "occurrence_count": 0})
            entry["occurrence_count"] += 1
        for attr, raw in element.attrib.items():
            attr_local = _local_name(attr)
            raw_text = _clean(raw)
            if raw_text is None:
                continue
            attribute_count += 1
            attrs[attr_local].append(raw_text)
            attr_path = f"{path}/@{attr_local}"
            observed_paths.add(attr_path)
            entry = field_catalog.setdefault(attr_path, {"field_path": attr_path, "namespace_uri": _namespace(attr), "local_name": attr_local, "value_kind": "attribute", "occurrence_count": 0})
            entry["occurrence_count"] += 1
        for child in list(element):
            visit(child, path)

    visit(root, "")
    catalog = sorted(field_catalog.values(), key=lambda row: row["field_path"])
    for row in catalog:
        row["document_count"] = 1
    return values, attrs, catalog, observed_paths, nodes, attribute_count


def _pick(values: dict[str, list[str]], attrs: dict[str, list[str]], name: str) -> str | None:
    return _first(values, name) or _first(attrs, name)


def _measure(values: dict[str, list[str]], attrs: dict[str, list[str]], tag: str, kind: str, period: str) -> dict[str, Any] | None:
    raw = _pick(values, attrs, tag)
    if raw is None:
        return None
    number = _number(raw)
    return {
        "measurement_kind": kind,
        "period_code": period,
        "value": number,
        "unit": "kWh" if kind.startswith("energy") else "kW",
        "source_path": f"//*[local-name()='{tag}']",
        "source_label": tag,
        "quality_status": _numeric_quality(raw, number),
    }


def parse_xml(xml: str | bytes, *, source_kind: str | None = None, raw_id: int | None = None,
              sha256: str | None = None, parser_version: str = PARSER_VERSION) -> dict[str, Any]:
    """Parse a CFDI/Addenda CFE XML into a deterministic normalized payload."""
    raw_bytes = xml.encode("utf-8") if isinstance(xml, str) else xml
    issues: list[dict[str, Any]] = []
    if re.search(br"<!DOCTYPE|<!ENTITY", raw_bytes, re.I):
        return _finalize({
            "parser_version": parser_version,
            "document": {},
            "invoice": {},
            "measurements": [],
            "charges": [],
            "ledger_lines": [],
            "history": [],
            "field_catalog": [],
            "observed_paths": [],
            "issues": [{"severity": "fatal", "rule_code": "UNSAFE_XML", "field_path": None, "raw_value": None}],
            "source": {"source_kind": source_kind, "raw_id": raw_id, "sha256": sha256},
        })
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        return _finalize({
            "parser_version": parser_version,
            "document": {},
            "invoice": {},
            "measurements": [],
            "charges": [],
            "ledger_lines": [],
            "history": [],
            "field_catalog": [],
            "observed_paths": [],
            "issues": [{"severity": "fatal", "rule_code": "XML_PARSE_ERROR", "field_path": None, "raw_value": str(exc)}],
            "source": {"source_kind": source_kind, "raw_id": raw_id, "sha256": sha256},
        })

    values, attrs, field_catalog, observed_paths, node_count, attribute_count = _walk(root)
    version = _pick(values, attrs, "Version")
    tipo = (_pick(values, attrs, "TipoDeComprobante") or "UNKNOWN").upper()
    if tipo not in {"I", "E", "P", "N", "T"}:
        tipo = "UNKNOWN"

    cfdi_uuid = _pick(values, attrs, "UUID")
    tarifa_reg = _pick(values, attrs, "TARIFA_REG")
    tarifa_legacy = _pick(values, attrs, "TARIFA")
    if tarifa_reg is None:
        issues.append({"severity": "warning", "rule_code": "TARIFA_REG_MISSING", "field_path": "TARIFA_REG", "raw_value": tarifa_legacy})
    elif tarifa_reg.upper() == "EMPTY":
        issues.append({"severity": "warning", "rule_code": "TARIFA_REG_EMPTY", "field_path": "TARIFA_REG", "raw_value": tarifa_reg})

    period_start = _parse_date(_pick(values, attrs, "FECDESDE"))
    period_end = _parse_date(_pick(values, attrs, "FECHASTA"))
    if period_start and period_end and period_end < period_start:
        issues.append({"severity": "error", "rule_code": "PERIOD_REVERSED", "field_path": "FECDESDE/FECHASTA", "raw_value": f"{period_start}/{period_end}"})
    billing_days = (period_end - period_start).days + 1 if period_start and period_end and period_end >= period_start else None

    rpu = _pick(values, attrs, "RPU")
    state = _pick(values, attrs, "NOMEST")
    municipio = _pick(values, attrs, "NOMPOB")
    division = _pick(values, attrs, "DIVISION") or _division(state, municipio)
    period_label = period_start.strftime("%Y-%m") if period_start else None

    root_attributes = { _local_name(key): _clean(value) for key, value in root.attrib.items() if _clean(value) is not None }
    document = {
        "document_key": f"uuid:{cfdi_uuid}" if cfdi_uuid else (f"sha256:{sha256}" if sha256 else None),
        "cfdi_uuid": cfdi_uuid,
        "tipo_comprobante": tipo,
        "cfdi_version": root_attributes.get("Version") or version,
        "serie": root_attributes.get("Serie"),
        "folio": root_attributes.get("Folio"),
        "fecha_emision": root_attributes.get("Fecha"),
        "moneda": root_attributes.get("Moneda"),
        "subtotal": _number(root_attributes.get("SubTotal")),
        "total": _number(root_attributes.get("Total")),
        "source_sha256": sha256,
    }
    invoice = {
        "rpu": rpu,
        "period_label": period_label,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "billing_days": billing_days,
        "tarifa_reg": tarifa_reg.upper() if tarifa_reg else None,
        "tarifa_legacy": tarifa_legacy.upper() if tarifa_legacy else None,
        "tarifa_legacy_normalized": TARIFA_ALIAS.get(tarifa_legacy.upper(), tarifa_legacy.upper()) if tarifa_legacy else None,
        "sicom": _pick(values, attrs, "SICOM") or _pick(values, attrs, "TarifaSICOM"),
        "division_code": division,
        "cp": (_pick(values, attrs, "RMU") or "")[:5] or None,
        "contracted_demand_kw": _number(_pick(values, attrs, "CARGA_CONTRATADA")),
    }

    measurements: list[dict[str, Any]] = []
    for tag, kind, period in (
        ("CONSUMO1F", "energy_active", "B"),
        ("CONSUMO2F", "energy_active", "I"),
        ("CONSUMO3F", "energy_active", "P"),
        ("CONSUMO4F", "energy_active", "S"),
        ("CONSUMO_R", "energy_active", "TOTAL"),
        ("DEMANDA1P", "demand_billed", "B"),
        ("DEMANDA2P", "demand_billed", "I"),
        ("DEMANDA3P", "demand_billed", "P"),
        ("DEMANDA4P", "demand_billed", "S"),
        ("DEMANDA", "demand_billed", "TOTAL"),
        ("DEMANDA_CAPACIDAD", "demand_billed", "TOTAL"),
        ("KVARH", "energy_reactive", "TOTAL"),
    ):
        measurement = _measure(values, attrs, tag, kind, period)
        if measurement is not None:
            measurements.append(measurement)
    fp_raw = _pick(values, attrs, "FP") or _pick(values, attrs, "FACTOR_POTENCIA")
    if fp_raw is not None:
        fp = _number(fp_raw)
        measurements.append({
            "measurement_kind": "other",
            "period_code": "TOTAL",
            "value": fp,
            "unit": "ratio",
            "source_path": "//*[local-name()='FP' or local-name()='FACTOR_POTENCIA']",
            "source_label": "FP",
            "quality_status": _numeric_quality(fp_raw, fp),
        })

    charges: list[dict[str, Any]] = []
    for number in range(1, 65):
        motivo = _exact(values, f"MOTIVO_REG_{number}") or _exact(attrs, f"MOTIVO_REG_{number}")
        amount_raw = _exact(values, f"IMPTE_TOT_REG_{number}") or _exact(attrs, f"IMPTE_TOT_REG_{number}")
        if motivo is None and amount_raw is None:
            continue
        if motivo is None:
            if _number(amount_raw) == 0:
                continue
            issues.append({"severity": "warning", "rule_code": "CHARGE_MOTIVO_MISSING", "field_path": f"IMPTE_TOT_REG_{number}", "raw_value": amount_raw})
            continue
        component, period = _motivo(motivo)
        amount = _number(amount_raw)
        charges.append({
            "source_slot": f"{number}",
            "motivo_code": motivo.upper(),
            "component_code": component,
            "period_code": period,
            "basis_kind": "other",
            "amount_total": amount,
            "concept_raw": motivo,
            "source_path": f"//*[local-name()='MOTIVO_REG_{number}']",
        })
        if component == "OTHER":
            issues.append({"severity": "warning", "rule_code": "UNKNOWN_MOTIVO", "field_path": f"MOTIVO_REG_{number}", "raw_value": motivo})

    ledger_lines: list[dict[str, Any]] = []
    for number in range(1, 65):
        concept = _numbered(values, attrs, number, "CONCEPTO_{number}", "Concepto{number}")
        amount_raw = _numbered(values, attrs, number, "IMPORTE_{number}", "Importe{number}")
        if concept is None and amount_raw is None:
            continue
        if concept is None:
            issues.append({"severity": "warning", "rule_code": "LEDGER_CONCEPT_MISSING", "field_path": f"IMPORTE_{number}", "raw_value": amount_raw})
            continue
        ledger_lines.append({
            "line_number": number,
            "concept_raw": concept,
            "amount": _number(amount_raw),
            "semantic_type": _semantic_line(concept),
            "source_path": f"//*[local-name()='CONCEPTO_{number}']",
        })

    history: list[dict[str, Any]] = []
    for number in range(1, 16):
        kwh_raw = _exact(values, f"CONSUMOR{number:02d}") or _exact(attrs, f"CONSUMOR{number:02d}")
        demand_raw = _exact(values, f"DEMANDAR{number:02d}") or _exact(attrs, f"DEMANDAR{number:02d}")
        if kwh_raw is None and demand_raw is None:
            continue
        history.append({
            "source_slot": number,
            "kwh": _number(kwh_raw),
            "demand_kw": _number(demand_raw),
            "source_path": f"//*[local-name()='CONSUMOR{number:02d}']",
        })

    # Detect values that are almost certainly portal sentinels.  They remain
    # in the evidence payload but are not silently promoted to a calculation.
    for measure in measurements:
        if measure["value"] is not None and abs(measure["value"]) >= 1_000_000_000:
            issues.append({"severity": "error", "rule_code": "SENTINEL_NUMERIC", "field_path": measure["source_path"], "raw_value": str(measure["value"])})
            measure["quality_status"] = "invalid"

    payload = {
        "parser_version": parser_version,
        "source": {"source_kind": source_kind, "raw_id": raw_id, "sha256": sha256},
        "document": document,
        "invoice": invoice,
        "measurements": measurements,
        "charges": charges,
        "ledger_lines": ledger_lines,
        "history": history,
        "field_catalog": field_catalog,
        "observed_paths": sorted(observed_paths),
        "stats": {"node_count": node_count, "attribute_count": attribute_count, "distinct_field_paths": len(field_catalog)},
        "issues": issues,
    }
    return _finalize(payload)


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["output_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def payload_to_db_rows(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert a parse payload into insert-ready rows for cfe.invoice_* tables."""
    document = payload.get("document") or {}
    source = payload.get("source") or {}
    invoice = payload.get("invoice") or {}
    return {
        "document": [document],
        "source": [source],
        "invoice": [invoice],
        "measurements": payload.get("measurements") or [],
        "charges": payload.get("charges") or [],
        "ledger_lines": payload.get("ledger_lines") or [],
        "history": payload.get("history") or [],
        "issues": payload.get("issues") or [],
    }


__all__ = ["PARSER_VERSION", "parse_xml", "payload_to_db_rows"]
