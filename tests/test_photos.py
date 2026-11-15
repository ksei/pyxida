import base64
import io
import tempfile
import unittest
import zipfile
from unittest.mock import Mock

import pymupdf
from PIL import Image
from docx import Document

from ai import WorkflowError
from app import Application
from documents import application_bundle
from formatting import html_document, pdf_document, word_document
from photos import DEFAULT_DOCUMENT_SETTINGS, document_settings, normalize_photo, photo_bytes
from storage import Store


def sample_photo(size=(800, 1200), mode='RGB', color='#cbd9de'):
    output = io.BytesIO()
    image = Image.new(mode, size, color)
    image.save(output, format='PNG')
    return base64.b64encode(output.getvalue()).decode()


class PhotoTests(unittest.TestCase):
    def test_normalization_produces_print_ready_jpeg_and_strips_metadata(self):
        original = Image.new('RGB', (1200, 800), 'blue')
        exif = Image.Exif(); exif[270] = 'Private image description'; exif[274] = 6
        output = io.BytesIO(); original.save(output, format='JPEG', exif=exif)
        photo = normalize_photo(base64.b64encode(output.getvalue()).decode())
        with Image.open(io.BytesIO(photo_bytes(photo))) as image:
            self.assertEqual(image.size, (640, 800))
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.mode, 'RGB')
            self.assertFalse(image.getexif())
        self.assertEqual(photo['mime'], 'image/jpeg')

    def test_transparent_images_get_a_white_background(self):
        photo = normalize_photo(sample_photo(mode='RGBA', color=(255, 0, 0, 0)))
        with Image.open(io.BytesIO(photo_bytes(photo))) as image:
            self.assertEqual(image.getpixel((100, 100)), (255, 255, 255))

    def test_invalid_small_or_oversized_images_are_rejected(self):
        for encoded in ('!!!', base64.b64encode(b'not an image').decode(),
                        sample_photo((50, 60)), base64.b64encode(b'x' * (5 * 1024 * 1024 + 1)).decode()):
            with self.subTest(encoded_length=len(encoded)), self.assertRaises(WorkflowError):
                normalize_photo(encoded)
        for settings in ({'photo_cv': 'yes', 'photo_letter': False}, {}, [], {'photo_cv': True, 'photo_letter': False, 'extra': True}):
            with self.subTest(settings=settings), self.assertRaises(WorkflowError):
                document_settings(settings)

    def test_saving_and_removing_a_photo_keeps_drafts_and_never_invokes_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory); agent = Mock()
            application = Application(store, agent)
            state = store.create('CV evidence', 'cv.txt', {'reply': 'Ready'}, {})
            other = store.create('Other CV', 'other.txt', {'reply': 'Ready'}, {})
            state.update(drafts={'existing': {'materials': 'saved draft'}}, search_revision=1)
            store.save(state)
            public = application.post('/api/document-settings', {'id': state['id'], 'photo': sample_photo()})
            self.assertNotIn('data', public['photo'])
            self.assertEqual(public['revision'], 1)
            self.assertEqual(public['search_revision'], 1)
            self.assertEqual(public['drafts'], state['drafts'])
            self.assertIsNone(store.get(other['id'])['photo'])
            application.post('/api/document-settings', {'id': state['id'], 'settings': {'photo_cv': False, 'photo_letter': True}})
            self.assertIsNotNone(store.get(state['id'])['photo'])
            application.post('/api/document-settings', {'id': state['id'], 'remove_photo': True})
            self.assertIsNone(store.get(state['id'])['photo'])
            self.assertEqual(agent.mock_calls, [])

    def test_existing_profiles_receive_appearance_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            state = store.create('CV', 'cv.txt', {'reply': 'Ready'}, {})
            del state['photo']; del state['document_settings']; store.save(state)
            loaded = store.get(state['id'])
            self.assertIsNone(loaded['photo'])
            self.assertEqual(loaded['document_settings'], DEFAULT_DOCUMENT_SETTINGS)

    def test_photo_exports_use_consistent_dimensions_without_losing_text(self):
        photo = photo_bytes(normalize_photo(sample_photo()))
        cv = '# Alex Example\nalex@example.com\n\n## Experience\n- Reviewed KYC evidence.'
        word = Document(io.BytesIO(word_document(cv, photo=photo)))
        self.assertEqual(len(word.inline_shapes), 1)
        self.assertAlmostEqual(word.inline_shapes[0].width.mm, 35, places=1)
        self.assertAlmostEqual(word.inline_shapes[0].height.mm, 43.75, places=1)
        self.assertIn('Alex Example', word.tables[0].cell(0, 0).text)
        with pymupdf.open(stream=pdf_document(cv, photo=photo), filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 1)
            images = pdf[0].get_image_info()
            self.assertEqual(len(images), 1)
            x0, y0, x1, y1 = images[0]['bbox']
            self.assertAlmostEqual((x1-x0) * 25.4 / 72, 35, places=1)
            self.assertAlmostEqual((y1-y0) * 25.4 / 72, 43.75, places=1)
            self.assertIn('Reviewed KYC evidence.', pdf[0].get_text().replace('\xa0', ' '))
        preview = html_document(cv, photo='/api/photo?id=example')
        self.assertIn('class="portrait"', preview)
        self.assertNotIn('class="portrait"', html_document(cv))

    def test_bundle_honors_independent_cv_and_letter_photo_choices(self):
        photo = normalize_photo(sample_photo())
        materials = {'cv': '# Alex Example\n\n## Experience\n- KYC review.',
                     'cover_letter': 'Dear hiring team,\n\nKYC experience.\n\nKind regards,\nAlex Example',
                     'email': 'Application', 'notes': 'Review', 'evidence': [], 'sources': []}
        for choices in ({'photo_cv': True, 'photo_letter': False}, {'photo_cv': False, 'photo_letter': True}):
            bundle = application_bundle({'title': 'Analyst', 'company': 'Example', 'url': 'https://example.com'}, materials, photo, choices)
            with zipfile.ZipFile(io.BytesIO(bundle)) as package:
                for prefix, option in (('tailored-cv', 'photo_cv'), ('cover-letter', 'photo_letter')):
                    with pymupdf.open(stream=package.read(prefix + '.pdf'), filetype='pdf') as pdf:
                        self.assertEqual(bool(pdf[0].get_images()), choices[option])
                    with zipfile.ZipFile(io.BytesIO(package.read(prefix + '.docx'))) as word:
                        self.assertEqual(any(name.startswith('word/media/') for name in word.namelist()), choices[option])


if __name__ == '__main__':
    unittest.main()
