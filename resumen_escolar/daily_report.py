"""Reporte diario deterministico de Resumen Escolar.

Este modulo transforma la evidencia ya capturada por Playwright en un estado
serializable y en texto plano. No contiene instrucciones para ningun LLM.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from .app import (
    PageSnapshot,
    SCHOOLNET_GRADE_SUBJECTS,
    authorized_event_lines,
    current_report_date,
    format_conducta_record_for_prompt,
    parse_conducta_counts,
    parse_conducta_records,
    schoolnet_grade_subject_key,
    schoolnet_prefer_canonical_p1,
    schoolnet_is_grade_subject,
)


REPORT_VERSION = 2
DEFAULT_REPORT_MAX_BYTES = 55_000
EMAIL_JSON_BEGIN = "--- RESUMEN_ESCOLAR_JSON_BEGIN ---"
EMAIL_JSON_END = "--- RESUMEN_ESCOLAR_JSON_END ---"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_norm(part) for part in parts)
    return prefix + "_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _snapshot_statuses(snapshots: list[PageSnapshot]) -> dict[str, str]:
    result: dict[str, str] = {}
    for snapshot in snapshots:
        platform = str(snapshot.platform or "")
        if "SchoolNet - Calificaciones" in platform:
            key = "SchoolNet calificaciones"
        elif "SchoolNet - Conducta" in platform:
            key = "SchoolNet conducta"
        elif platform.startswith("Calendario SSCC"):
            key = "Calendario SSCC"
        elif platform.startswith("Google Classroom"):
            key = "Google Classroom"
        else:
            continue
        result[key] = str(snapshot.status or "unknown")
    return result


_SCHOOLNET_ASSESSMENTS_HEADER = "DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES"
_SCHOOLNET_GRADE_VALUE = re.compile(r"^[1-7][,.][0-9]$")
_SCHOOLNET_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
_SCHOOLNET_NON_ASSESSMENT_CELLS = {"p1", "p2", "nf", "promedio", "promedios"}


def _assessment_rows(text: str) -> dict[str, list[dict[str, Any]]]:
    """Read the visible evaluation rows captured after opening each subject."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _SCHOOLNET_ASSESSMENTS_HEADER)
    except StopIteration:
        return {}

    labels = {schoolnet_grade_subject_key(subject): subject for subject in SCHOOLNET_GRADE_SUBJECTS}
    current_subject = ""
    result: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for line in lines[start + 1 :]:
        subject_match = re.match(r"^\s*ASIGNATURA\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if subject_match:
            raw_subject = subject_match.group(1).strip()
            current_subject = labels.get(schoolnet_grade_subject_key(raw_subject), raw_subject)
            if not schoolnet_is_grade_subject(current_subject):
                current_subject = ""
            continue
        row_match = re.match(r"^\s*Fila\s+(\d+)\s*\|\s*(.*)$", line, re.IGNORECASE)
        if not current_subject or not row_match:
            continue

        row_number = row_match.group(1)
        cells = [cell.strip() for cell in row_match.group(2).split("|") if cell.strip()]
        normalized = [schoolnet_grade_subject_key(cell) for cell in cells]
        if any(cell in _SCHOOLNET_NON_ASSESSMENT_CELLS for cell in normalized):
            continue
        subject_key = schoolnet_grade_subject_key(current_subject)
        if subject_key and any(cell == subject_key for cell in normalized):
            # This is the parent subject row with semester averages, not an evaluation.
            continue

        grades = [cell.replace(".", ",") for cell in cells if _SCHOOLNET_GRADE_VALUE.fullmatch(cell.replace(".", ","))]
        if not grades:
            continue
        date = next((match.group(0) for cell in cells if (match := _SCHOOLNET_DATE.search(cell))), "")
        generic_cells = {"fecha", "nota", "calificacion", "calificacion final", "evaluacion", "ponderacion", "pond"}
        title = next(
            (
                cell
                for cell in cells
                if not _SCHOOLNET_GRADE_VALUE.fullmatch(cell.replace(".", ","))
                and not _SCHOOLNET_DATE.search(cell)
                and schoolnet_grade_subject_key(cell) not in generic_cells
            ),
            "",
        )
        detail_cells = [
            cell
            for cell in cells
            if cell != title
            and not _SCHOOLNET_GRADE_VALUE.fullmatch(cell.replace(".", ","))
            and not _SCHOOLNET_DATE.search(cell)
        ]
        identity_parts = [current_subject, title, date, detail_cells]
        if not title:
            identity_parts.append(row_number)
        row_identity = _id("assessment", *identity_parts)
        if row_identity in seen:
            continue
        seen.add(row_identity)
        result.setdefault(current_subject, []).append(
            {
                "id": row_identity,
                "title": title,
                "date": date,
                "grades": grades,
                "details": detail_cells,
            }
        )
    return result


def _grades(snapshots: list[PageSnapshot]) -> list[dict[str, Any]]:
    for snapshot in snapshots:
        if "SchoolNet - Calificaciones" not in snapshot.platform or not snapshot.text:
            continue
        all_text = snapshot.text
        assessments = _assessment_rows(all_text)
        canonical_text = schoolnet_prefer_canonical_p1(all_text)
        rows_by_subject: dict[str, dict[str, Any]] = {}
        subject_labels = {schoolnet_grade_subject_key(subject): subject for subject in SCHOOLNET_GRADE_SUBJECTS}
        for line in canonical_text.splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3 or not parts[0] or parts[0].lower() in {"asignatura", "formato"}:
                continue
            if parts[0].lower().startswith(("fuente", "regla", "para la", "calificaciones", "formato:")):
                continue
            key = schoolnet_grade_subject_key(parts[0])
            if key not in subject_labels:
                continue
            rows_by_subject.setdefault(
                key,
                {"subject": subject_labels[key], "p1": parts[1], "p2": parts[2], "assessments": []},
            )
        for subject, subject_assessments in assessments.items():
            key = schoolnet_grade_subject_key(subject)
            rows_by_subject.setdefault(
                key,
                {"subject": subject_labels.get(key, subject), "p1": "", "p2": "", "assessments": []},
            )["assessments"] = subject_assessments
        rows = list(rows_by_subject.values())
        if rows:
            return rows
    return []


def _conduct(snapshots: list[PageSnapshot]) -> dict[str, Any]:
    texts = [s.text for s in snapshots if "SchoolNet - Conducta" in s.platform and s.text]
    counts: dict[str, int] = {"positivas": 0, "negativas": 0, "neutras": 0}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in texts:
        parsed = parse_conducta_counts(text)
        if parsed:
            counts = parsed
        for record in parse_conducta_records(text, "SchoolNet - Conducta"):
            record_id = str(record.get("id") or "")
            if record_id and record_id not in seen:
                seen.add(record_id)
                records.append(record)
    records.sort(key=lambda r: str((r.get("metadata") or {}).get("sort_date") or ""), reverse=True)
    latest: dict[str, Any] | None = None
    if records:
        metadata = records[0].get("metadata") or {}
        latest = {
            "id": records[0].get("id", ""),
            "date": metadata.get("sort_date") or metadata.get("fecha", ""),
            "type": metadata.get("tipo", ""),
            "subject": metadata.get("asignatura", ""),
            "category": metadata.get("categoria", ""),
            "observation": metadata.get("observacion", ""),
            "detail": format_conducta_record_for_prompt(records[0]),
        }
    return {"counts": counts, "latest": latest}


def _events(snapshots: list[PageSnapshot], report_date: dt.date) -> list[dict[str, str]]:
    end = report_date + dt.timedelta(days=9)
    lines = authorized_event_lines(snapshots, report_date, end)
    events: list[dict[str, str]] = []
    for line in lines:
        parts = [part.strip() for part in line.split("|")]
        values: dict[str, str] = {}
        for part in parts:
            if ":" in part:
                key, value = part.split(":", 1)
                values[_norm(key)] = value.strip()
        date = values.get("fecha", "")
        if not date:
            date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", line)
            date = date_match.group(0) if date_match else ""
        source = parts[0] if parts else ""
        event_type = values.get("tipo", "")
        subject = values.get("asignatura", "")
        if not values and len(parts) >= 3:
            offset = 1 if parts[0].startswith("Evento") else 0
            event_type = parts[offset + 1] if len(parts) > offset + 1 else "Evento"
            subject = parts[offset + 2] if len(parts) > offset + 2 else ""
        event_type = event_type or "Evento"
        title = values.get("tema/texto", "") or values.get("titulo", "")
        detail = " | ".join(parts[1:]).strip()
        events.append({
            "id": _id("event", date, event_type, subject, title or detail),
            "date": date,
            "type": event_type,
            "subject": subject,
            "title": title,
            "detail": detail,
            "source": source,
        })
    return events


def build_state(snapshots: list[PageSnapshot], report_date: dt.date | None = None) -> dict[str, Any]:
    day = report_date or current_report_date()
    grades = _grades(snapshots)
    assessment_count = sum(len(row.get("assessments", [])) for row in grades)
    return {
        "version": REPORT_VERSION,
        "report_date": day.isoformat(),
        "window_start": day.isoformat(),
        "window_end": (day + dt.timedelta(days=9)).isoformat(),
        "sources": _snapshot_statuses(snapshots),
        "grades": grades,
        "grade_details": {
            "subjects_with_assessments": sum(bool(row.get("assessments")) for row in grades),
            "assessment_count": assessment_count,
        },
        "conduct": _conduct(snapshots),
        "events": _events(snapshots, day),
    }


def _event_line(event: dict[str, str]) -> str:
    title = event.get("title") or event.get("detail") or "Sin detalle"
    return f"{event.get('date','')} | {event.get('type','Evento')} | {event.get('subject') or 'Sin asignatura'} | {title}"


def _diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if not previous:
        return ["No existe un reporte diario anterior para comparar."]
    changes: list[str] = []
    old_grades = {_norm(row.get("subject")): row for row in previous.get("grades", [])}
    new_grades = {_norm(row.get("subject")): row for row in current.get("grades", [])}
    for key, row in new_grades.items():
        if key not in old_grades:
            changes.append(f"[NUEVO] Calificacion: {row['subject']} | P1: {row.get('p1','')} | P2: {row.get('p2','')}")
        elif any(old_grades[key].get(col, "") != row.get(col, "") for col in ("p1", "p2")):
            old = old_grades[key]
            changes.append(f"[ACTUALIZADO] Calificacion: {row['subject']} | P1: {old.get('p1','')} -> {row.get('p1','')} | P2: {old.get('p2','')} -> {row.get('p2','')}")
    for key, row in old_grades.items():
        if key not in new_grades:
            changes.append(f"[ELIMINADO] Calificacion: {row['subject']}")

    old_assessments = {
        str(item.get("id") or ""): (row, item)
        for row in previous.get("grades", [])
        for item in row.get("assessments", [])
        if item.get("id")
    }
    new_assessments = {
        str(item.get("id") or ""): (row, item)
        for row in current.get("grades", [])
        for item in row.get("assessments", [])
        if item.get("id")
    }
    for key, (row, item) in new_assessments.items():
        label = item.get("title") or item.get("date") or "evaluacion sin titulo"
        note = ", ".join(item.get("grades", []))
        if key not in old_assessments:
            changes.append(f"[NUEVA NOTA] {row['subject']} | {label} | {note}")
        elif old_assessments[key][1].get("grades") != item.get("grades"):
            old_note = ", ".join(old_assessments[key][1].get("grades", []))
            changes.append(f"[ACTUALIZADA NOTA] {row['subject']} | {label} | {old_note} -> {note}")
    for key, (row, item) in old_assessments.items():
        if key not in new_assessments:
            label = item.get("title") or item.get("date") or "evaluacion sin titulo"
            changes.append(f"[ELIMINADA NOTA] {row['subject']} | {label}")

    old_counts = (previous.get("conduct") or {}).get("counts") or {}
    new_counts = (current.get("conduct") or {}).get("counts") or {}
    for label in ("positivas", "negativas", "neutras"):
        if old_counts.get(label) != new_counts.get(label):
            changes.append(f"[ACTUALIZADO] Conducta {label}: {old_counts.get(label, 0)} -> {new_counts.get(label, 0)}")
    old_latest = (previous.get("conduct") or {}).get("latest") or {}
    new_latest = (current.get("conduct") or {}).get("latest") or {}
    if new_latest and not old_latest:
        changes.append(f"[NUEVO] Ultima anotacion: {new_latest.get('detail','')}")
    elif old_latest and new_latest and old_latest != new_latest:
        changes.append(f"[ACTUALIZADO] Ultima anotacion: {new_latest.get('detail','')}")

    old_events = {event.get("id"): event for event in previous.get("events", [])}
    new_events = {event.get("id"): event for event in current.get("events", [])}
    for key, event in new_events.items():
        if key not in old_events:
            changes.append(f"[NUEVO] Actividad: {_event_line(event)}")
        elif old_events[key] != event:
            changes.append(f"[ACTUALIZADO] Actividad: {_event_line(event)}")
    today = current.get("report_date", "")
    for key, event in old_events.items():
        if key not in new_events and str(event.get("date", "")) >= today:
            changes.append(f"[ELIMINADO] Actividad: {_event_line(event)}")
    old_sources = previous.get("sources") or {}
    for source, status in (current.get("sources") or {}).items():
        if old_sources.get(source) != status:
            changes.append(f"[ACTUALIZADO] Estado {source}: {old_sources.get(source, 'sin registro')} -> {status}")
    return changes or ["Sin cambios detectados respecto al reporte diario anterior."]


def render_report(current: dict[str, Any], previous: dict[str, Any] | None = None, max_bytes: int = DEFAULT_REPORT_MAX_BYTES) -> str:
    lines = [
        "RESUMEN ESCOLAR DIARIO - GABITO",
        f"Fecha: {current['report_date']}",
        "",
        "1. CAMBIOS DESDE EL REPORTE ANTERIOR",
        *_diff(previous, current),
        "",
        "2. INFORMACIÓN COMPLETA",
        "2.1 CALIFICACIONES",
        "Asignatura | P1 | P2",
    ]
    grades = current.get("grades", [])
    lines.extend(f"{r.get('subject','')} | {r.get('p1','')} | {r.get('p2','')}" for r in grades)
    if not grades:
        lines.append("Sin calificaciones disponibles.")
    grade_details = current.get("grade_details") or {}
    lines.append(
        "Notas individuales incluidas en el JSON: "
        f"{grade_details.get('assessment_count', 0)} en "
        f"{grade_details.get('subjects_with_assessments', 0)} asignaturas."
    )
    conduct = current.get("conduct") or {}
    counts = conduct.get("counts") or {}
    lines += [
        "",
        "2.2 CONDUCTA",
        f"Positivas: {counts.get('positivas', 0)} | Negativas: {counts.get('negativas', 0)} | Neutras: {counts.get('neutras', 0)}",
    ]
    latest = conduct.get("latest")
    lines.append(f"Última anotación: {(latest or {}).get('detail', 'No detectada') if latest else 'No detectada'}")
    lines += ["", "2.3 PRÓXIMOS 10 DÍAS"]
    events = current.get("events", [])
    lines.extend(_event_line(event) for event in events)
    if not events:
        lines.append("No hay pruebas, tareas o eventos detectados.")
    lines += ["", "2.4 ESTADO DE FUENTES"]
    lines.extend(f"{source}: {status}" for source, status in sorted((current.get("sources") or {}).items()))
    text = "\n".join(lines).strip() + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Solo se recorta la agenda y el diagnóstico final; cambios y núcleo quedan intactos.
    marker = "\n[Se omitieron detalles secundarios por limite de correo. Consulte el respaldo diario.]\n"
    core = text.split("\n2.3 PRÓXIMOS 10 DÍAS\n", 1)[0]
    return (core + "\n2.3 PRÓXIMOS 10 DÍAS\n" + marker).encode("utf-8")[:max_bytes].decode("utf-8", "ignore").rstrip() + "\n"

def render_email_message(
    current: dict[str, Any],
    previous: dict[str, Any] | None = None,
    max_bytes: int = DEFAULT_REPORT_MAX_BYTES,
) -> str:
    """Render a human summary followed by the exact state as valid JSON."""
    json_payload = json.dumps(
        current,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    json_section = (
        "3. DATOS ESTRUCTURADOS\n"
        "JSON válido para lectura automática. La fecha report_date identifica la corrida.\n"
        f"{EMAIL_JSON_BEGIN}\n{json_payload}\n{EMAIL_JSON_END}\n"
    )
    json_bytes = len(json_section.encode("utf-8"))
    if json_bytes >= max_bytes:
        raise ValueError(
            "El estado JSON excede el límite del correo OCI; revise el volumen de evidencia estructurada."
        )

    report_budget = max_bytes - json_bytes - 2
    report = render_report(current, previous, max_bytes=report_budget).rstrip()
    message = f"{report}\n\n{json_section}"
    if len(message.encode("utf-8")) > max_bytes:
        raise ValueError("El correo estructurado excede el límite configurado.")
    return message
