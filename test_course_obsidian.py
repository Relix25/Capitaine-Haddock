import tempfile
import unittest
from pathlib import Path

import course_obsidian


def item(source="teams:AAIT", key="message-1", title="Message    important", body="Premier paragraphe.\n\nDeuxième paragraphe."):
    return {
        "source": source,
        "key": key,
        "title": title,
        "body": body,
        "url": "https://teams.cloud.microsoft/message/1",
    }


def snapshot(*, status="ok", items=None, fresh_items=None):
    course = {
        "label": "Advanced Artificial Intelligence Techniques",
        "sources": [
            {
                "source": "teams:AAIT",
                "status": status,
                "checked_utc": "2026-11-12T13:59:27Z",
                "detail": "Publications/réponses chargées dans Général.",
            },
            {
                "source": "moodle:AAIT",
                "status": "ok",
                "checked_utc": "2026-11-12T13:59:27Z",
                "detail": "Activités et dates Moodle.",
            },
        ],
        "items": items or [],
    }
    if fresh_items is not None:
        course["fresh_items"] = fresh_items
    return {"version": 1, "checked_utc": "2026-11-12T13:59:27Z", "courses": {"AAIT": course}}


class CourseObsidianTests(unittest.TestCase):
    def test_content_paragraphs_sources_and_bucharest_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            message = item()
            course_obsidian.update_vault(snapshot(items=[message], fresh_items=[message]), vault)
            note = vault / "Notes" / "AAIT" / "AAIT - Veille automatique.md"
            text = note.read_text(encoding="utf-8")

            self.assertIn("Message important", text)
            self.assertIn("Premier paragraphe.\n\nDeuxième paragraphe.", text)
            self.assertIn("https://teams.cloud.microsoft/message/1", text)
            self.assertIn("2026-11-12 15:59:27 +0200 Europe/Bucharest", text)
            self.assertIn("Général", text)
            self.assertIn("[[AAIT - Accueil]]", text)
            self.assertIn("aucun changement signalé", text)

    def test_idempotent_for_same_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            message = item()
            data = snapshot(items=[message], fresh_items=[message])
            course_obsidian.update_vault(data, vault)
            paths = [vault / "Veille - dernier contrôle.md", vault / "Notes" / "AAIT" / "AAIT - Veille automatique.md"]
            first = [path.read_bytes() for path in paths]
            course_obsidian.update_vault(data, vault)
            self.assertEqual(first, [path.read_bytes() for path in paths])

    def test_manual_course_page_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            note = vault / "Notes" / "AAIT" / "AAIT - Veille automatique.md"
            note.parent.mkdir(parents=True)
            note.write_text("# Ma note manuelle\n", encoding="utf-8")
            with self.assertRaises(course_obsidian.ManualPageError):
                course_obsidian.update_vault(snapshot(), vault)
            self.assertEqual(note.read_text(encoding="utf-8"), "# Ma note manuelle\n")

    def test_manual_index_is_protected_before_course_notes_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            index = vault / "Veille - dernier contrôle.md"
            index.write_text("# Tableau manuel\n", encoding="utf-8")
            with self.assertRaises(course_obsidian.ManualPageError):
                course_obsidian.update_vault(snapshot(), vault)
            self.assertFalse((vault / "Notes" / "AAIT" / "AAIT - Veille automatique.md").exists())
            self.assertEqual(index.read_text(encoding="utf-8"), "# Tableau manuel\n")

    def test_failed_source_is_visible_and_known_content_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            message = item(body="Texte connu avant la panne.")
            course_obsidian.update_vault(snapshot(items=[message], fresh_items=[message]), vault)

            failed = snapshot(status="network_error", items=[message], fresh_items=[])
            course_obsidian.update_vault(failed, vault)
            text = (vault / "Notes" / "AAIT" / "AAIT - Veille automatique.md").read_text(encoding="utf-8")
            self.assertIn("ERREUR : `network_error`", text)
            self.assertIn("Texte connu avant la panne.", text)
            self.assertIn("Contenu connu conservé", text)
            self.assertIn("Collecte réussie; aucun changement signalé", text)  # Moodle still succeeded.

    def test_symlink_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            outside = root / "outside"
            vault.mkdir()
            outside.mkdir()
            (vault / "Notes").mkdir()
            try:
                (vault / "Notes" / "AAIT").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(course_obsidian.UnsafeVaultError):
                course_obsidian.update_vault(snapshot(), vault)
            self.assertEqual(list(outside.iterdir()), [])

    def test_unknown_or_sensitive_fields_are_rejected(self):
        data = snapshot()
        data["cookies"] = []
        with self.assertRaises(course_obsidian.SnapshotError):
            course_obsidian.validate_snapshot(data)

        data = snapshot()
        data["courses"]["AAIT"]["items"] = [dict(item(), url="file:///etc/passwd")]
        with self.assertRaises(course_obsidian.SnapshotError):
            course_obsidian.validate_snapshot(data)


if __name__ == "__main__":
    unittest.main()
