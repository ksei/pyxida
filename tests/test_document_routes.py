import io
import json
import tempfile
import threading
import unittest
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pymupdf

from app import Application, make_server
from storage import Store
from test_photos import sample_photo


class DocumentRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.store = Store(cls.directory.name)
        cls.state = cls.store.create("Candidate evidence", "CV.txt", {"reply": "Profile ready."}, {})
        cls.materials = {
            "cv": "# Alex Example\nalex@example.com\n\n## Experience\n- Reviewed **KYC files** and investigated transaction alerts.",
            "cover_letter": "Application for Analyst\n\nDear hiring team,\n\nI reviewed KYC files.\n\nKind regards,\nAlex Example",
            "email": "Subject: Application", "notes": "Review before sending.",
            "evidence": [{"claim": "KYC review", "source": "Reviewed KYC files."}], "sources": []}
        cls.state["drafts"]["example"] = {"job": {"title": "Analyst", "company": "Example Bank", "url": "https://example.com/job"},
                                           "materials": cls.materials, "language": "English", "revision": 1}
        cls.store.save(cls.state)
        cls.server = make_server(Application(cls.store), 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.directory.cleanup()

    def document_url(self, **params):
        values = {"id": self.state["id"], "job": "example", "kind": "cv", "format": "html", **params}
        return self.base + "/api/document?" + "&".join(f"{k}={v}" for k, v in values.items())

    def test_preview_uses_formatted_html_and_allows_only_same_origin_framing(self):
        with urlopen(self.document_url()) as response:
            text = response.read().decode()
            self.assertIn('<strong>KYC files</strong>', text)
            self.assertIn('href="/document.css"', text)
            self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])
        with urlopen(self.base + "/document.css") as response:
            self.assertIn(".document .name", response.read().decode())

    def test_pdf_and_word_downloads_have_correct_types_and_attachment_names(self):
        with urlopen(self.document_url(format="pdf")) as response:
            self.assertEqual(response.headers["Content-Type"], "application/pdf")
            self.assertIn('tailored-cv.pdf', response.headers["Content-Disposition"])
            self.assertTrue(response.read().startswith(b"%PDF"))
        with urlopen(self.document_url(kind="cover_letter", format="docx")) as response:
            self.assertIn("wordprocessingml.document", response.headers["Content-Type"])
            with zipfile.ZipFile(io.BytesIO(response.read())) as document:
                self.assertIn("word/styles.xml", document.namelist())

    def test_application_package_retains_original_text_and_adds_both_pdfs(self):
        with urlopen(f'{self.base}/api/export?id={self.state["id"]}&job=example') as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as bundle:
                self.assertIn("cover-letter.pdf", bundle.namelist())
                self.assertIn("tailored-cv.pdf", bundle.namelist())
                self.assertEqual(bundle.read("tailored-cv.md").decode(), self.materials["cv"])

    def test_unrecognized_document_kind_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.document_url(kind="cv-source"))
        try:
            self.assertEqual(caught.exception.code, 400)
            self.assertIn("Choose a saved CV", json.load(caught.exception)["error"])
        finally:
            caught.exception.close()

    def test_bundled_fonts_and_crop_script_are_available(self):
        with urlopen(self.base + '/fonts/SourceSans3-Regular.ttf') as response:
            self.assertEqual(response.headers['Content-Type'], 'font/ttf')
            self.assertTrue(response.read().startswith(b'\x00\x01\x00\x00'))
        with urlopen(self.base + '/photo-crop.js') as response:
            self.assertIn('photoCropRect', response.read().decode())
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/fonts/manifest.json')
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_photo_upload_preview_download_toggle_and_removal(self):
        def post(data):
            request = Request(self.base + '/api/document-settings',
                              json.dumps({'id': self.state['id'], **data}).encode(),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request) as response: return json.load(response)
        try:
            state = post({'photo': sample_photo()})
            self.assertNotIn('data', state['photo'])
            with urlopen(f'{self.base}/api/photo?id={self.state["id"]}') as response:
                self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                self.assertTrue(response.read().startswith(b'\xff\xd8'))
            with urlopen(self.document_url()) as response:
                self.assertIn('class="portrait"', response.read().decode())
            with urlopen(self.document_url(kind='cover_letter')) as response:
                self.assertNotIn('class="portrait"', response.read().decode())
            with urlopen(self.document_url(format='pdf')) as response:
                with pymupdf.open(stream=response.read(), filetype='pdf') as pdf:
                    self.assertEqual(len(pdf[0].get_images()), 1)
            post({'settings': {'photo_cv': False, 'photo_letter': True}})
            with urlopen(self.document_url()) as response:
                self.assertNotIn('class="portrait"', response.read().decode())
            with urlopen(self.document_url(kind='cover_letter')) as response:
                self.assertIn('class="portrait"', response.read().decode())
            post({'remove_photo': True})
            with self.assertRaises(HTTPError) as caught:
                urlopen(f'{self.base}/api/photo?id={self.state["id"]}')
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        finally:
            post({'remove_photo': True, 'settings': {'photo_cv': True, 'photo_letter': False}})


if __name__ == "__main__":
    unittest.main()
