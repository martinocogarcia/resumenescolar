from __future__ import annotations

import argparse
import re
from pathlib import Path


SECTION_RE = re.compile(
    r"----- SchoolNet - Calificaciones -----\n.*?----- FIN SchoolNet - Calificaciones -----",
    re.DOTALL,
)


RULE_REPLACEMENTS = {
    "- Seccion CALIFICACIONES P1 con tabla Asignatura / Nota usando las notas P1 extraidas. Incluye asignaturas sin nota solo si hay espacio; prioriza asignaturas con nota.": "- Seccion CALIFICACIONES P1 con tabla Asignatura / Nota usando solo la tabla CALIFICACIONES P1 CANONICAS SCHOOLNET. Incluye asignaturas sin nota solo si hay espacio; prioriza asignaturas con nota.",
    "- En calificaciones, usa como fuente principal CALIFICACIONES P1 CANONICAS SCHOOLNET si aparece.": "- En calificaciones, usa como fuente unica CALIFICACIONES P1 CANONICAS SCHOOLNET si aparece.",
    "- En calificaciones, muestra siempre el promedio por asignatura de la columna P1; nunca uses las columnas 1, 2 o 3 como nota final de asignatura.": "- En calificaciones, copia exactamente los pares Asignatura | P1 de esa tabla. Nunca uses las columnas 1, 2, 3, P2 o NF como nota final de asignatura.",
    "- En calificaciones, incluye todas las asignaturas visibles y deja en blanco las que no tengan P1.": "- En calificaciones, si una asignatura tiene P1 vacio, dejala en blanco u omitela si no hay espacio; no infieras el valor desde otra columna.\n- En calificaciones, no agregues asignaturas que no esten en la tabla canonica P1.",
    "- Para responder preguntas posteriores como 'de donde viene el 6,7 de Musica', busca primero en DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES bajo ASIGNATURA: Musica. Si aparece una fila interna con nombre de evaluacion o actividad y esa nota, esa es la fuente de la nota.\n": "",
    "- Para explicar de donde sale una nota de una asignatura, usa el bloque DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES. Las filas Tipo=Item bajo la misma Asignatura padre son evaluaciones o componentes de esa asignatura.\n": "",
    "- Ejemplo: si Lenguaje tiene P1 6,8 y debajo aparece Item | Lenguaje y Comunicacion | Unidad 1 - Infografia | | 6,8 | ... entonces el 6,8 corresponde a la evaluacion Unidad 1 - Infografia.\n": "",
}


def replace_rules(text: str) -> str:
    for old, new in RULE_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def canonical_block(section: str) -> str:
    marker = "CALIFICACIONES P1 CANONICAS SCHOOLNET"
    positions: list[int] = []
    start = 0
    while True:
        index = section.find(marker, start)
        if index < 0:
            break
        positions.append(index)
        start = index + 1

    for index in reversed(positions):
        block_lines: list[str] = []
        for line in section[index:].splitlines():
            stripped = line.strip()
            if block_lines and (
                stripped.startswith("DETALLE ")
                or stripped.startswith("TEXTO CRUDO")
                or stripped.startswith("LECTURA CRUDA")
                or stripped.startswith("----- FIN ")
            ):
                break
            block_lines.append(line)
        block = "\n".join(block_lines).strip()
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        has_format = any(line == "Formato: Asignatura | P1" for line in lines)
        has_grades = sum(1 for line in lines if line.count("|") == 1 and not line.startswith("Formato:")) >= 2
        if has_format and has_grades:
            return block
    return ""


def strengthen_canonical(block: str) -> str:
    lines = block.splitlines()
    strengthened = [
        "CALIFICACIONES P1 CANONICAS SCHOOLNET",
        "Fuente autoritativa para la infografia: columna P1 del bloque visual estructurado de SchoolNet.",
        "Regla obligatoria: copiar exactamente estos valores Asignatura | P1; las columnas 1, 2 y 3 son notas parciales y no deben mostrarse como promedio de asignatura.",
        "Si cualquier otro bloque contiene otra nota para la misma asignatura, ignorarla para la tabla de calificaciones.",
        "Formato: Asignatura | P1",
    ]
    for line in lines:
        if line.count("|") == 1 and not line.startswith("Formato:"):
            strengthened.append(line)
    return "\n".join(strengthened)


def sanitize_section(match: re.Match[str]) -> str:
    section = match.group(0)
    block = canonical_block(section)
    if not block:
        return section

    before_marker = "TEXTO VISIBLE / EVIDENCIA"
    metadata = section.split(before_marker, 1)[0].rstrip()
    return "\n".join(
        [
            metadata,
            "",
            before_marker,
            strengthen_canonical(block),
            "----- FIN SchoolNet - Calificaciones -----",
        ]
    )


def sanitize_prompt(text: str) -> str:
    text = replace_rules(text)
    return SECTION_RE.sub(sanitize_section, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deja SchoolNet Calificaciones solo con la tabla canonica P1.")
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    args = parser.parse_args()

    input_path = Path(args.input_path)
    output_path = Path(args.output_path)
    text = input_path.read_text(encoding="utf-8", errors="replace")
    sanitized = sanitize_prompt(text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sanitized, encoding="utf-8")

    print(f"bytes={output_path.stat().st_size}")
    print(f"canonical_sections={sanitized.count('CALIFICACIONES P1 CANONICAS SCHOOLNET')}")
    print(f"raw_grade_detail_sections={sanitized.count('DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES')}")
    print(f"subject_grade_detail_sections={sanitized.count('DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
