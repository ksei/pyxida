"""Deterministic quality checks on the rendered application documents.

An applicant tracking system reads the PDF text layer, not the rendered page.
These checks render the same PDFs the user downloads, extract their text and
verify page limits, literal contact details, garbled glyphs, unfilled
placeholders, clichés and listing keyword coverage. Missing keywords are
reported as gaps; they are never inserted automatically.
"""
import re

from ai import CV_PAGE_LIMIT, LETTER_PAGE_LIMIT, WorkflowError, normalized
from formatting import cv_identity, pdf_document

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d ()./-]{7,}\d")
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{2,60}\]")
LINK = re.compile(r"\[[^\]]+\]\([^)]+\)")
CLICHES = ("passionate about", "team player", "hit the ground running", "leverage my", "synergies",
           "synergy", "results-driven", "think outside the box", "go-getter", "detail-oriented self-starter",
           "i believe i would be a great fit", "perfect fit", "dynamic environment", "proven track record")


def pdf_text(data):
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return len(document), "\n".join(page.get_text() for page in document)


def item(label, status, detail):
    return {"label": label, "status": status, "detail": detail}


def keyword_coverage(keywords, text):
    haystack = normalized(text)
    found, missing = [], []
    for keyword in dict.fromkeys(k.strip() for k in keywords if k and k.strip()):
        (found if normalized(keyword) in haystack else missing).append(keyword)
    return {"found": found, "missing": missing}


def quality_checks(materials, keywords=(), cv_photo=None, letter_photo=None):
    identity = cv_identity(materials["cv"])
    try:
        cv_pages, cv_text = pdf_text(pdf_document(materials["cv"], "cv", identity, photo=cv_photo))
        letter_pages, letter_text = pdf_text(pdf_document(materials["cover_letter"], "cover_letter", identity, photo=letter_photo))
    except ImportError:
        return {"items": [item("PDF checks", "skipped", "Install requirements.txt to enable PDF text checks.")],
                "keywords": keyword_coverage(keywords, materials["cv"]), "cv_pages": None, "letter_pages": None}
    except WorkflowError as e:
        return {"items": [item("PDF rendering", "fail", str(e))],
                "keywords": keyword_coverage(keywords, materials["cv"]), "cv_pages": None, "letter_pages": None}
    items = [
        item("CV length", "pass" if cv_pages <= CV_PAGE_LIMIT else "fail",
             f"{cv_pages} page{'s' if cv_pages != 1 else ''} (limit {CV_PAGE_LIMIT})."),
        item("Cover letter length", "pass" if letter_pages <= LETTER_PAGE_LIMIT else "fail",
             f"{letter_pages} page{'s' if letter_pages != 1 else ''} (limit {LETTER_PAGE_LIMIT})."),
    ]
    garbled = [m for m in ("�", "(cid:") if m in cv_text]
    items.append(item("Text layer", "fail" if garbled else "pass",
                      "Garbled glyphs in the extracted text; an ATS may misread the CV." if garbled
                      else "The CV text extracts cleanly for applicant tracking systems."))
    source = LINK.sub("", materials["cv"])
    contacts = list(dict.fromkeys(EMAIL.findall(source) + [p.strip() for p in PHONE.findall(source) if sum(c.isdigit() for c in p) >= 9]))
    flat = " ".join(cv_text.split())
    missing_contacts = [c for c in contacts if " ".join(c.split()) not in flat]
    if not contacts:
        items.append(item("Contact details", "warn", "No email address or telephone number appears as text in the CV."))
    elif missing_contacts:
        items.append(item("Contact details", "fail", "Not present in the PDF text layer: " + ", ".join(missing_contacts)))
    else:
        items.append(item("Contact details", "pass", "Email and telephone appear as literal text."))
    placeholders = sorted({p for text in (materials["cv"], materials["cover_letter"], materials.get("email", ""))
                           for p in PLACEHOLDER.findall(LINK.sub("", text))})
    items.append(item("Placeholders", "warn" if placeholders else "pass",
                      "Fill before sending: " + ", ".join(placeholders[:12]) if placeholders else "No unfilled placeholders."))
    letter_flat = normalized(letter_text)
    cliches = [c for c in CLICHES if c in letter_flat]
    items.append(item("Cover letter phrasing", "warn" if cliches else "pass",
                      "Consider replacing: " + ", ".join(f"“{c}”" for c in cliches) if cliches else "No stock phrases detected."))
    coverage = keyword_coverage(keywords, cv_text)
    total = len(coverage["found"]) + len(coverage["missing"])
    if total:
        share = round(100 * len(coverage["found"]) / total)
        items.append(item("Listing keywords", "pass" if share >= 70 else "warn",
                          f"{len(coverage['found'])} of {total} listing terms appear in the CV ({share}%). "
                          "Add missing terms only where your experience genuinely supports them."))
    return {"items": items, "keywords": coverage, "cv_pages": cv_pages, "letter_pages": letter_pages}
