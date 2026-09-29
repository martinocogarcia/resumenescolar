import datetime as dt
import json
import unittest

from resumen_escolar.app import (
    BrowserController,
    PageSnapshot,
    SCHOOLNET_GRADES_BY_SUBJECT_SCRIPT,
    SCHOOLNET_GRADES_DETAIL_SCRIPT,
)
from resumen_escolar.daily_report import (
    EMAIL_JSON_BEGIN,
    EMAIL_JSON_END,
    build_state,
    render_email_message,
    render_report,
)


def snapshot(platform, text, status="ok"):
    return PageSnapshot(platform, platform, "", text, status, [], {})


class DailyReportTests(unittest.TestCase):
    def test_schoolnet_subject_rows_reach_published_json(self):
        class Frame:
            url = "https://schoolnet.colegium.com/grades"

            def evaluate(self, script):
                if script == SCHOOLNET_GRADES_DETAIL_SCRIPT:
                    return "DETALLE CRUDO ESTRUCTURADO SCHOOLNET CALIFICACIONES\nAsignatura | x | Matematica | | | | | 6,0 | 6,5 |"
                if script == SCHOOLNET_GRADES_BY_SUBJECT_SCRIPT:
                    return (
                        "DETALLE POR ASIGNATURA SCHOOLNET CALIFICACIONES\n"
                        "Fuente: apertura secuencial de cada asignatura\n"
                        "ASIGNATURA: Matematica\n"
                        "Fila 1 | Matematica | 6,0 | 6,5\n"
                        "Fila 2 | Evaluacion | Fecha | Nota\n"
                        "Fila 3 | Control de fracciones | 20/09/2026 | 6,3\n"
                        "Fila 4 | Proyecto | 22/09/2026 | 7,0"
                    )
                return ""

        class Page:
            frames = [Frame()]

        extracted, metadata = BrowserController()._extract_schoolnet_grades_table(Page())
        state = build_state([snapshot("SchoolNet - Calificaciones", extracted)], dt.date(2026, 9, 25))
        grade = state["grades"][0]
        self.assertEqual(metadata["assessment_detail_sources"], 1)
        self.assertEqual((grade["p1"], grade["p2"]), ("6,0", "6,5"))
        self.assertEqual([item["grades"] for item in grade["assessments"]], [["6,3"], ["7,0"]])
        self.assertEqual(state["grade_details"]["assessment_count"], 2)
        email = render_email_message(state)
        published = json.loads(email.split(EMAIL_JSON_BEGIN, 1)[1].split(EMAIL_JSON_END, 1)[0])
        self.assertEqual(published["grades"][0]["assessments"], grade["assessments"])

    def test_first_report_has_complete_sections_and_ten_day_window(self):
        day = dt.date(2026, 7, 24)
        current = build_state([
            snapshot("SchoolNet - Calificaciones", "CALIFICACIONES P1/P2 CANONICAS SCHOOLNET\nAsignatura | P1 | P2\nMatematica | 6,0 | 6,5"),
            snapshot("SchoolNet - Conducta", "Anotaciones Positivas Anotaciones Negativas Anotaciones Neutras 2 1 0\nVISTA SCHOOLNET CONDUCTA: Positivas\nFila tabla 1 | 23/07/2026 | Motivo | Prof | Matematica | Bien | Positiva"),
            snapshot("Calendario SSCC", "Evento 1 | 2026-07-24 | Prueba | Matematica"),
        ], day)
        report = render_report(current)
        self.assertIn("1. CAMBIOS DESDE EL REPORTE ANTERIOR", report)
        self.assertIn("No existe un reporte diario anterior", report)
        self.assertIn("Asignatura | P1 | P2", report)
        self.assertIn("2.3 PRÓXIMOS 10 DÍAS", report)
        self.assertEqual(current["window_end"], "2026-08-02")

    def test_grade_and_event_changes_are_semantic(self):
        old = {
            "report_date": "2026-07-24", "grades": [{"subject": "Matematica", "p1": "6,0", "p2": ""}],
            "conduct": {"counts": {"positivas": 0, "negativas": 0, "neutras": 0}, "latest": None},
            "events": [{"id": "event_1", "date": "2026-07-25", "type": "Tarea", "subject": "Lenguaje", "title": "Ensayo", "detail": "", "source": ""}],
            "sources": {"SchoolNet calificaciones": "ok"},
        }
        new = {**old, "grades": [{"subject": "Matematica", "p1": "6,0", "p2": "6,5"}], "events": []}
        report = render_report(new, old)
        self.assertIn("[ACTUALIZADO] Calificacion: Matematica", report)
        self.assertIn("[ELIMINADO] Actividad:", report)

    def test_email_contains_machine_readable_state_within_oci_limit(self):
        current = {
            "version": 1,
            "report_date": "2026-09-25",
            "window_start": "2026-09-25",
            "window_end": "2026-10-04",
            "sources": {"Google Classroom": "ok"},
            "grades": [{"subject": "Matematica", "p1": "6,0", "p2": "6,5"}],
            "conduct": {"counts": {"positivas": 1, "negativas": 0, "neutras": 0}, "latest": None},
            "events": [{
                "id": "event_1",
                "date": "2026-09-28",
                "type": "Prueba",
                "subject": "Matematica",
                "title": "Unidad 3",
                "detail": "Fracciones y problemas",
                "source": "Google Classroom",
            }],
        }

        email = render_email_message(current, max_bytes=8_000)

        self.assertLessEqual(len(email.encode("utf-8")), 8_000)
        payload = email.split(EMAIL_JSON_BEGIN, 1)[1].split(EMAIL_JSON_END, 1)[0].strip()
        self.assertEqual(json.loads(payload), current)
        self.assertIn("JSON válido para lectura automática", email)


if __name__ == "__main__":
    unittest.main()
