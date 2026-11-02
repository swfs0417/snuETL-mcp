"""Canvas 접속 없이 저장 경로와 이름 설정을 확인한다."""
import importlib.util
import sys
import tempfile
import types
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('materials_under_test', ROOT / 'materials.py')
materials = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'canvas_client': types.ModuleType('canvas_client'),
                              'links': types.ModuleType('links')}):
    spec.loader.exec_module(materials)


class MaterialsTests(unittest.TestCase):
    def test_names_and_terms(self):
        self.assertEqual(materials.display_course_name('2026-2 Experimental Linguistics (001)'), '실험언어학')
        self.assertEqual(materials.display_course_name('2026-2 사회언어학 (001)'), '사회언어학')
        for code, folder in [(1, '1'), (2, '2'), (3, 'S'), (4, 'W')]:
            self.assertEqual(materials._term_folder(f'2026-{code} 과목'), f'2026-{folder}')

    def test_existing_folder_and_other_semester(self):
        with tempfile.TemporaryDirectory() as base:
            old = Path(base) / '2025-2' / '실험언어학'
            old.mkdir(parents=True)
            name = '2026-2 Experimental Linguistics (001)'
            path, how = materials.resolve_course_dir(name, base)
            self.assertEqual(path, Path(base) / '2026-2' / '실험언어학')
            self.assertEqual(how, 'computed')
            existing = Path(base) / '2026-2' / unicodedata.normalize('NFD', '실험언어학')
            existing.mkdir(parents=True)
            self.assertEqual(materials.resolve_course_dir(name, base), (existing, 'existing'))

    def test_json_reload_and_validation(self):
        with tempfile.TemporaryDirectory() as base:
            config = Path(base) / 'names.json'
            with patch.object(materials, '_ALIASES_PATH', config):
                config.write_text('{"Course": "과목"}')
                self.assertEqual(materials.display_course_name('Course'), '과목')
                config.write_text('{"Course": "수업"}')
                self.assertEqual(materials.display_course_name('Course'), '수업')
                config.write_text('{"Course": null}')
                with self.assertRaises(ValueError):
                    materials.display_course_name('Course')

    def test_flat_download_targets_and_duplicate_guard(self):
        file = types.SimpleNamespace(id=1, display_name='notes.pdf', folder_id=1)
        course = types.SimpleNamespace(name='2026-2 Experimental Linguistics',
            get_folders=lambda: [types.SimpleNamespace(id=1, full_name='course files/week1')],
            get_files=lambda: [file])
        canvas = types.SimpleNamespace(get_course=lambda _: course)
        with tempfile.TemporaryDirectory() as base, patch.object(materials.cc, 'get_canvas', return_value=canvas, create=True):
            result = materials._course_targets(1, base, include_linked=False)
            self.assertEqual(result[4][0][1], Path(base) / '2026-2/실험언어학/notes.pdf')
            course.get_files = lambda: [file, types.SimpleNamespace(id=2, display_name='notes.pdf', folder_id=1)]
            with self.assertRaises(ValueError):
                materials._course_targets(1, base, include_linked=False)

    def test_organize_flat_and_dry_run(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            source = root / 'week1' / 'notes.pdf'
            source.parent.mkdir()
            source.write_text('lecture')
            result = (None, '과목', root, 'existing', [(None, source, None)])
            with patch.object(materials, '_course_targets', return_value=result):
                preview = materials.organize_course_files(1, dry_run=True)
                self.assertEqual(preview['plan'][0]['to'], str(root / 'notes.pdf'))
                self.assertTrue(source.exists())
                materials.organize_course_files(1)
                self.assertEqual((root / 'notes.pdf').read_text(), 'lecture')
                self.assertFalse(source.exists())


if __name__ == '__main__':
    unittest.main()
