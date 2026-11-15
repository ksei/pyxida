"""Local CV extraction and formatted application packages."""
import base64
import csv
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .ai import WorkflowError
from .formatting import cv_identity, word_document, pdf_document
from .photos import DEFAULT_DOCUMENT_SETTINGS, photo_bytes

MAX_UPLOAD = 8 * 1024 * 1024
MAX_TEXT = 60000


def extract_cv(filename, encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as e:
        raise WorkflowError("The upload could not be read. Please select it again.") from e
    if len(data) > MAX_UPLOAD:
        raise WorkflowError("Please upload a CV smaller than 8 MB.")
    extension = Path(filename).suffix.lower()
    try:
        if extension in (".txt", ".md"):
            text = data.decode("utf-8-sig")
        elif extension == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as e:
                raise WorkflowError("PDF support needs pypdf. Install requirements.txt or paste your CV text.") from e
            reader = PdfReader(io.BytesIO(data))
            if len(reader.pages) > 40:
                raise WorkflowError("Please upload a CV with at most 40 pages.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif extension == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > 4 * 1024 * 1024:
                    raise WorkflowError("The Word document is too large to read.")
                root = ET.fromstring(z.read(info))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                text = "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns))
                                  for p in root.findall(".//w:p", ns))
        else:
            raise WorkflowError("Use a PDF, DOCX, TXT or Markdown CV.")
    except WorkflowError:
        raise
    except Exception as e:
        raise WorkflowError("This file could not be read. Try another copy or paste your CV text.") from e
    return validate_cv(text)


LINKEDIN_FILES = ("Profile.csv", "Positions.csv", "Education.csv", "Skills.csv", "Certifications.csv",
                  "Languages.csv", "Projects.csv", "Honors.csv", "Publications.csv", "Courses.csv",
                  "Recommendations_Received.csv", "Volunteering.csv")
SUPPORTING_TEXT = 40000


def csv_text(data, title):
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))
    lines = [f"## {title}"]
    for row in rows[:200]:
        values = [f"{k}: {v.strip()}" for k, v in row.items() if k and v and v.strip()]
        if values:
            lines.append("- " + "; ".join(values))
    return "\n".join(lines) if len(lines) > 1 else ""


def linkedin_text(data):
    """Readable text from a LinkedIn data export (ZIP of CSV files), skipping messages and contacts."""
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = {Path(n).name: n for n in z.namelist()}
        for name in LINKEDIN_FILES:
            if name in names:
                info = z.getinfo(names[name])
                if info.file_size > 4 * 1024 * 1024:
                    continue
                text = csv_text(z.read(info), name.removesuffix(".csv").replace("_", " "))
                if text:
                    parts.append(text)
    if not parts:
        raise WorkflowError("This ZIP does not look like a LinkedIn data export (no Profile, Positions or Skills files).")
    return "\n\n".join(parts)


def extract_supporting(filename, encoded):
    """Text of a supporting document: PDF, Word, text, CSV or a LinkedIn export ZIP."""
    extension = Path(filename).suffix.lower()
    if extension in (".zip", ".csv"):
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as e:
            raise WorkflowError("The upload could not be read. Please select it again.") from e
        if len(data) > MAX_UPLOAD:
            raise WorkflowError("Please upload a file smaller than 8 MB.")
        try:
            text = linkedin_text(data) if extension == ".zip" else csv_text(data, Path(filename).stem)
        except WorkflowError:
            raise
        except Exception as e:
            raise WorkflowError("This file could not be read. Try another copy.") from e
    else:
        text = extract_cv(filename, encoded)
    text = text.strip()
    if len(text) < 40:
        raise WorkflowError("This document has too little readable text. Scanned documents need text extraction first.")
    return text[:SUPPORTING_TEXT]


def validate_cv(text):
    if not isinstance(text, str) or len(text.strip()) < 80:
        raise WorkflowError("Please provide more CV text. For a scanned PDF, paste the text or upload a text-based copy.")
    if len(text) > MAX_TEXT:
        raise WorkflowError("Please provide a CV with fewer than 60,000 characters.")
    return text.strip()


def application_bundle(job, drafts, photo=None, settings=None, checks=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        identity = cv_identity(drafts["cv"])
        settings = settings or DEFAULT_DOCUMENT_SETTINGS
        for key, name in (("cv", "tailored-cv"), ("cover_letter", "cover-letter")):
            picture = photo_bytes(photo) if settings["photo_cv" if key == "cv" else "photo_letter"] else None
            z.writestr(f"{name}.md", drafts[key])
            z.writestr(f"{name}.docx", word_document(drafts[key], key, identity, photo=picture))
            z.writestr(f"{name}.pdf", pdf_document(drafts[key], key, identity, photo=picture))
        z.writestr("email.txt", drafts["email"])
        notes = f'# Draft application: {job["title"]} at {job["company"]}\n\nListing: {job["url"]}\n\n'
        notes += drafts["notes"] + "\n\n## Candidate evidence\n"
        for e in drafts["evidence"]:
            notes += f'\n- {e["claim"]}\n  Source: {e["source"]}\n'
        review = drafts.get("review")
        if review:
            notes += f'\n## Independent review{"" if review.get("applied") else " (revision not applied)"}\n\n{review["summary"]}\n'
            for issue in review["issues"]:
                notes += f'\n- [{issue["severity"]}] {issue["document"]}: {issue["issue"]} Fix: {issue["fix"]}'
            if review["stretch_claims"]:
                notes += "\n\nClaims to check: " + "; ".join(review["stretch_claims"])
            for point in review["company_points"]:
                notes += f'\n- Company fact: {point["point"]} ({point["url"]})'
            notes += "\n"
        if checks:
            notes += "\n## Quality checks\n" + "".join(f'\n- {c["status"].upper()} {c["label"]}: {c["detail"]}' for c in checks["items"])
            if checks["keywords"]["missing"]:
                notes += "\n\nListing terms not in the CV: " + ", ".join(checks["keywords"]["missing"])
            notes += "\n"
        notes += "\n## Listing sources\n" + "\n".join(s["url"] for s in drafts.get("sources", []))
        z.writestr("review-notes.md", notes)
    return buffer.getvalue()
