import io
import unittest
import uuid
import zipfile
from xml.etree import ElementTree as ET
from html.parser import HTMLParser

import pymupdf
from docx import Document

from pyxida.formatting import cv_identity, document_blocks, html_document, pdf_document, word_document
from pyxida.document_fonts import BODY_FONT, DISPLAY_FONT, FONT_DIR, FONT_FILES

CV = """# Alex Example
**AML & KYC specialist**
Zurich | alex@example.com | +41 00 000 00 00
**Draft tailored for: Example Bank**

## Professional experience
### Analyst | Example Bank
**2021–2025**
- Reviewed **KYC files** and investigated transaction alerts.
- Coordinated *complex* investigations with colleagues.
- Wrote clear escalation reports.

## Languages
- French: fluent
- German: B2
"""
LETTER = """**Draft — application for Compliance Analyst**

Alex Example
Zurich
alex@example.com

[Date]
Hiring team
Example Bank

Dear hiring team,

My KYC review and investigation experience would support this role.

Kind regards,
Alex Example
"""


class FormattingTests(unittest.TestCase):
    def test_word_has_real_bullets_emphasis_hierarchy_and_a4(self):
        document = Document(io.BytesIO(word_document(CV)))
        self.assertAlmostEqual(document.sections[0].page_width.mm, 210, places=1)
        self.assertAlmostEqual(document.sections[0].page_height.mm, 297, places=1)
        bullet = next(p for p in document.paragraphs if "Reviewed" in p.text)
        self.assertEqual(bullet.style.name, "List Bullet")
        self.assertFalse(bullet.text.startswith("-"))
        self.assertTrue(next(r for r in bullet.runs if r.text == "KYC files").bold)
        role = next(p for p in document.paragraphs if p.text == "Analyst")
        self.assertTrue(role.paragraph_format.keep_with_next)
        self.assertIn("Example Bank\t2021–2025", [p.text for p in document.paragraphs])
        self.assertEqual(document.styles['Normal'].font.name, BODY_FONT)
        self.assertEqual(document.styles['Application Name'].font.name, DISPLAY_FONT)
        self.assertGreater(document.styles["Application Name"].font.size,
                           document.styles["Application Section"].font.size)
        self.assertFalse(any("Draft tailored" in p.text for p in document.paragraphs))

    def test_cover_letter_has_header_then_subject_then_salutation(self):
        blocks = document_blocks(LETTER, "cover_letter", cv_identity(CV))
        styles = [b["style"] for b in blocks]
        self.assertEqual(styles[0], "Name")
        self.assertLess(styles.index("Subject"), styles.index("Salutation"))
        subject = next(b for b in blocks if b["style"] == "Subject")
        self.assertEqual(subject["text"], "Application for Compliance Analyst")
        self.assertTrue(any(b["text"] == "[Date]" for b in blocks))
        document = Document(io.BytesIO(word_document(LETTER, "cover_letter", cv_identity(CV))))
        self.assertEqual(document.paragraphs[0].text, "Alex Example")

    def test_combined_employer_and_dates_use_the_same_timeline_layout(self):
        text = '# Alex Example\n\n## Experience\n### Analyst\n**Example Bank | 2021–2025**\n- Reviewed KYC files.'
        metadata = next(block for block in document_blocks(text) if block['style'] == 'Metadata')
        self.assertEqual(metadata['left_text'], 'Example Bank')
        self.assertEqual(metadata['right_text'], '2021–2025')
        word = Document(io.BytesIO(word_document(text)))
        self.assertIn('Example Bank\t2021–2025', [p.text for p in word.paragraphs])

    def test_internal_tailoring_labels_are_removed_from_cv_letterheads(self):
        for label in ('Draft — tailored to Specialist, Example', 'Draft tailored for: Example', 'Tailored for: Example'):
            cv = '# Alex Example\n**' + label + '**\nalex@example.com\n\n## Experience\n- Reviewed KYC files.'
            self.assertFalse(any('tailored' in block['text'].lower() for block in document_blocks(cv)))

    def test_a_missing_letterhead_reuses_only_actual_cv_contact_details(self):
        text = "Application for Analyst\n\nDear hiring team,\n\nI reviewed KYC files.\n\nSincerely,\nAlex Example"
        blocks = document_blocks(text, "cover_letter", cv_identity(CV))
        self.assertEqual(blocks[0]["text"], "Alex Example")
        self.assertIn("alex@example.com", blocks[1]["text"])
        self.assertFalse(any("Example Street" in b["text"] for b in blocks))

    def test_html_preview_escapes_input_and_rejects_script_links(self):
        rendered = html_document('# <script>alert(1)</script>\n\n## Experience\n- [Click](javascript:evil)\n- **True fact**')
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("<strong>True fact</strong>", rendered)
        class Tags(HTMLParser):
            def __init__(self): super().__init__(); self.tags = []
            def handle_starttag(self, tag, attrs): self.tags.append(tag)
        parser = Tags(); parser.feed(rendered)
        self.assertNotIn("script", parser.tags)
        self.assertIn("ul", parser.tags)

    def test_pdf_keeps_candidate_text_readable_and_has_a4_pages(self):
        data = pdf_document(CV)
        with pymupdf.open(stream=data, filetype="pdf") as document:
            self.assertEqual(len(document), 1)
            self.assertAlmostEqual(document[0].rect.width, 595.276, places=2)
            text = document[0].get_text().replace('\xa0', ' ')
            self.assertIn("Alex Example", text)
            self.assertIn("2021–2025", text)
            self.assertNotIn("**", text)
            self.assertNotIn("Draft tailored", text)
            dates = [span for block in document[0].get_text('dict')['blocks'] if 'lines' in block
                     for line in block['lines'] for span in line['spans'] if '2021–2025' in span['text']]
            self.assertEqual(len(dates), 1)
            self.assertAlmostEqual(dates[0]['bbox'][2], 541.418, places=1)
            fonts = [font[3] for font in document[0].get_fonts()]
            self.assertTrue(any(BODY_FONT in font for font in fonts))
            self.assertTrue(any(DISPLAY_FONT in font for font in fonts))

    def test_long_pdf_preserves_content_and_role_opening_on_one_page(self):
        text = '# Alex Example\n\n## Experience\n### First role\n2020–2024\n'
        text += '\n'.join(f'- First role achievement {i}: ' + 'Reviewed transactions and KYC evidence. ' * 3 for i in range(22))
        text += '\n### Second role\n2018–2020\n- Opening detail one\n- Opening detail two\n- Opening detail three\n- Final detail\n'
        with pymupdf.open(stream=pdf_document(text), filetype='pdf') as document:
            self.assertGreater(len(document), 1)
            pages = [page.get_text().replace('\xa0', ' ') for page in document]
            self.assertTrue(any(all(value in page for value in ('Second role', '2018–2020', 'Opening detail one', 'Opening detail two', 'Opening detail three')) for page in pages))
            self.assertIn('Final detail', '\n'.join(pages))

    def test_word_embeds_all_font_variants_with_reversible_obfuscation(self):
        namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
        with zipfile.ZipFile(io.BytesIO(word_document(CV))) as package:
            table = ET.fromstring(package.read('word/fontTable.xml'))
            relationships = ET.fromstring(package.read('word/_rels/fontTable.xml.rels'))
            targets = {rel.get('Id'): rel.get('Target') for rel in relationships}
            for filename, (family, variant) in FONT_FILES.items():
                font = next(node for node in table if node.get('{%s}name' % namespaces['w']) == family)
                embedded = font.find('w:' + variant, namespaces)
                key = uuid.UUID(embedded.get('{%s}fontKey' % namespaces['w']).strip('{}'))
                relation = embedded.get('{%s}id' % namespaces['r'])
                data = bytearray(package.read('word/' + targets[relation]))
                for i in range(32): data[i] ^= key.bytes[::-1][i % 16]
                self.assertEqual(bytes(data), (FONT_DIR / filename).read_bytes())


if __name__ == '__main__':
    unittest.main()
