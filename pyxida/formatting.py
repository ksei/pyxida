"""Professional application layouts shared by Word, PDF and browser previews."""
import html
import io
import re
from urllib.parse import urlsplit

from .ai import WorkflowError
from .document_fonts import BODY_FONT, DISPLAY_FONT, FONT_DIR, embed_word_fonts

PAGE_MARGIN_MM = 19
PHOTO_WIDTH_MM = 35
PHOTO_HEIGHT_MM = 43.75

NAVY = "233E53"
INK = "26333D"
MUTED = "61717D"
SALUTATION = re.compile(r"^(Dear\b|Sehr geehrte|Guten Tag|Madame\b|Monsieur\b|Gentil[ei]\b|Egregi[oa]?\b|To the hiring)", re.I)
CLOSING = re.compile(r"^(Sincerely|Kind regards|Yours|Best regards|Mit freundlichen|Freundliche Gr|Cordialement|Avec mes|Cordiali|Distinti)", re.I)
SUBJECT = re.compile(r"^(Subject\s*:|Re\s*:|Application\b|Bewerbung\b|Betreff\s*:|Candidature\b|Objet\s*:|Oggetto\s*:)", re.I)
INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_)|\[[^\]]+\]\([^\s)]+\))")


def safe_link(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and not parsed.username and not parsed.password) or (
                    parsed.scheme == "mailto" and bool(parsed.path))
    except ValueError:
        return False


def inline_parts(text):
    parts = []
    for token in INLINE.split(text):
        if not token:
            continue
        part = {"text": token, "bold": False, "italic": False, "url": None}
        if token.startswith(("**", "__")) and token.endswith(token[:2]):
            part.update(text=token[2:-2], bold=True)
        elif token.startswith(("*", "_")) and token.endswith(token[0]):
            part.update(text=token[1:-1], italic=True)
        elif token.startswith("[") and "](" in token:
            label, url = token[1:-1].split("](", 1)
            part["text"] = label
            if safe_link(url):
                part["url"] = url
        parts.append(part)
    return parts


def plain_text(text):
    return "".join(p["text"] for p in inline_parts(text))


def markdown_blocks(text):
    blocks = []
    gap = False
    for line in text.splitlines():
        if not line.strip():
            gap = True
            continue
        stripped = line.strip()
        if stripped in ("---", "***", "___", "```", "```markdown"):
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        bullet = re.match(r"^(\s*)(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if heading:
            blocks.append({"kind": "heading", "level": len(heading[1]), "text": heading[2], "gap": gap})
        elif bullet:
            blocks.append({"kind": "bullet", "level": min(2, len(bullet[1]) // 2), "text": bullet[2], "gap": gap})
        elif line.startswith(("  ", "\t")) and blocks and blocks[-1]["kind"] == "bullet" and not gap:
            blocks[-1]["text"] += " " + stripped
        else:
            blocks.append({"kind": "paragraph", "text": stripped, "gap": gap})
        gap = False
    return blocks


def cv_identity(text):
    """Reuse supplied name/contact lines; never infer an address or qualification."""
    blocks = markdown_blocks(text)
    name = plain_text(blocks[0]["text"]) if blocks else ""
    contact = []
    for block in blocks[1:]:
        if block["kind"] == "heading":
            break
        value = plain_text(block["text"])
        if "@" in value or re.search(r"(?:\+\d|https?://|linkedin\.)", value, re.I):
            contact.append(value)
    return {"name": name, "contact": list(dict.fromkeys(contact))}


def document_blocks(text, kind="cv", identity=None):
    blocks = markdown_blocks(text)
    if kind == "cv":
        # Tailoring labels belong in the app, not in the candidate's document header.
        first_section = next((i for i, b in enumerate(blocks[1:], 1) if b["kind"] == "heading"), len(blocks))
        blocks = [b for i, b in enumerate(blocks) if not (i < first_section and
                  re.match(r"^(?:Draft\s*(?:[—–:-]\s*)?)?tailored\s+(?:for|to)\b", plain_text(b["text"]), re.I))]
        in_header = True
        next_is_meta = False
        for index, block in enumerate(blocks):
            if index == 0:
                block["style"] = "Name"
            elif block["kind"] == "heading":
                in_header = False
                block["style"] = "Section" if block["level"] <= 2 else "Entry"
                next_is_meta = block["style"] == "Entry"
            elif in_header:
                block["style"] = "Subtitle" if index == 1 and block["text"].startswith("**") else "Contact"
            elif next_is_meta and block["kind"] == "paragraph" and len(plain_text(block["text"])) < 120:
                block["style"] = "Metadata"
                next_is_meta = False
            else:
                block["style"] = "Bullet" if block["kind"] == "bullet" else "Body"
                next_is_meta = False
        header = [b for b in blocks if b["style"] in ("Name", "Subtitle", "Contact")]
        if header:
            header[-1]["divider"] = True
        # Separate job titles from employer/date metadata for a cleaner timeline.
        for index, block in enumerate(blocks):
            if block["style"] == "Entry" and " | " in block["text"]:
                title, company = block["text"].split(" | ", 1)
                if index + 1 < len(blocks) and blocks[index + 1]["style"] == "Metadata":
                    block["text"] = title
                    metadata = blocks[index + 1]
                    metadata.update(left_text=company, right_text=metadata["text"],
                                    text=company + " · " + metadata["text"])
            elif block["style"] == "Metadata" and " | " in plain_text(block["text"]):
                # Codex may already put the employer and dates on the same line.
                # Give these drafts the same alignment without changing their facts.
                company, dates = plain_text(block["text"]).rsplit(" | ", 1)
                if re.search(r"\b(?:19|20)\d{2}\b|\[(?:dates?|datum|data)", dates, re.I):
                    block.update(left_text=company, right_text=dates)
        return blocks

    identity = identity or {"name": "", "contact": []}
    for block in blocks:
        value = plain_text(block["text"])
        if re.match(r"^Draft\s*[—–:-]\s*(?:application|bewerbung|candidature)\b", value, re.I):
            value = re.sub(r"^Draft\s*[—–:-]\s*", "", value, flags=re.I)
            block["text"] = value[:1].upper() + value[1:]
    greeting = next((i for i, b in enumerate(blocks) if SALUTATION.match(plain_text(b["text"]))), len(blocks))
    name = identity.get("name", "")
    sender_index = next((i for i, b in enumerate(blocks[:greeting])
                         if name and plain_text(b["text"]).casefold() == name.casefold()), None)
    if sender_index is None and name:
        prefix = [{"kind": "paragraph", "style": "Name", "text": name, "gap": False}]
        prefix += [{"kind": "paragraph", "style": "Contact", "text": line, "gap": False}
                   for line in identity.get("contact", [])]
        prefix[-1]["divider"] = True
        blocks = prefix + blocks
        greeting += len(prefix)
    sender_active = False
    closing = False
    for index, block in enumerate(blocks):
        if "style" in block:
            continue
        value = plain_text(block["text"])
        if SUBJECT.match(value) or (index < greeting and block["text"].startswith("**") and block["text"].endswith("**")):
            block["style"] = "Subject"
            sender_active = False
        elif index < greeting and name and value.casefold() == name.casefold():
            block["style"] = "Name"
            sender_active = True
        elif index < greeting and re.match(r"^\[(?:date|datum|data)\]$", value, re.I):
            block["style"] = "Address"
            sender_active = False
        elif sender_active and index < greeting and not block["gap"]:
            block["style"] = "Contact"
        elif SALUTATION.match(value):
            block["style"] = "Salutation"
            sender_active = False
        elif CLOSING.match(value):
            block["style"] = "Closing"
            closing = True
        elif closing:
            block["style"] = "Signature"
        elif index < greeting:
            block["style"] = "Address"
            sender_active = False
        else:
            block["style"] = "Bullet" if block["kind"] == "bullet" else "Body"
    header = [b for b in blocks[:greeting] if b["style"] in ("Name", "Contact")]
    if header:
        header[-1]["divider"] = True
    # Existing drafts sometimes put the subject above the sender's name.
    # Keep the letterhead first and the application subject just before the greeting.
    prefix = blocks[:greeting]
    subjects = [b for b in prefix if b["style"] == "Subject"]
    if subjects:
        blocks = [b for b in prefix if b["style"] != "Subject"] + subjects + blocks[greeting:]
    return blocks


def heading_lead_end(blocks, index):
    following = index + 1
    while following < len(blocks) and blocks[following]["style"] in ("Section", "Entry", "Metadata"):
        following += 1
    if blocks[index]["style"] == "Entry" and following < len(blocks) and blocks[following]["style"] == "Bullet":
        # Start an employment entry with enough detail to read naturally, rather
        # than putting its title and one lone bullet at the foot of a page.
        for _ in range(2):
            if following + 1 < len(blocks) and blocks[following + 1]["style"] == "Bullet":
                following += 1
    return following


DOCUMENT_CSS = """
@font-face { font-family: 'Source Sans 3'; src: url('/fonts/SourceSans3-Regular.ttf'); font-weight: 400; font-style: normal; }
@font-face { font-family: 'Source Sans 3'; src: url('/fonts/SourceSans3-Bold.ttf'); font-weight: 700; font-style: normal; }
@font-face { font-family: 'Source Sans 3'; src: url('/fonts/SourceSans3-It.ttf'); font-weight: 400; font-style: italic; }
@font-face { font-family: 'Source Sans 3'; src: url('/fonts/SourceSans3-BoldIt.ttf'); font-weight: 700; font-style: italic; }
@font-face { font-family: 'Source Serif 4'; src: url('/fonts/SourceSerif4-Regular.ttf'); font-weight: 400; }
@page { size: A4; margin: 19mm; }
* { box-sizing: border-box; }
body { margin: 0; background: #e9edf0; color: #26333d; }
.sheet { max-width: 210mm; min-height: 297mm; padding: 19mm; margin: 20px auto; background: white; box-shadow: 0 3px 18px #233e5318; }
.document { font-family: 'Source Sans 3', sans-serif; font-size: 10.5pt; line-height: 1.23; overflow-wrap: break-word; }
.letter { font-size: 11.25pt; line-height: 1.36; }
.document p { margin: 0 0 4pt; orphans: 2; widows: 2; }
.document .name { font-family: 'Source Serif 4', serif; font-size: 30pt; line-height: 1.12; font-weight: 400; color: #233e53; margin: 0 0 7pt; }
.document .subtitle { font-size: 11pt; font-weight: 700; color: #233e53; margin: 0 0 7pt; }
.document .contact { font-size: 9.25pt; line-height: 1.32; color: #61717d; margin: 0 0 3pt; }
.document .divider { padding-bottom: 12pt; border-bottom: .8pt solid #233e53; margin-bottom: 14pt; }
.document .section { font-size: 9.5pt; font-weight: 700; color: #233e53; text-transform: uppercase; letter-spacing: 1pt; margin: 15pt 0 7pt; border-bottom: .4pt solid #d6dfe5; padding-bottom: 4pt; page-break-after: avoid; }
.document .entry { font-size: 11.25pt; line-height: 1.22; font-weight: 700; color: #26333d; margin: 10pt 0 3pt; page-break-after: avoid; }
.document .metadata { font-size: 9.5pt; color: #61717d; margin: 0 0 6pt; page-break-after: avoid; }
.document .entry-meta { width: 100%; border-collapse: collapse; margin: 0 0 6pt; }
.document .entry-meta td { padding: 0; font-size: 9.5pt; color: #61717d; vertical-align: top; }
.document .entry-meta .dates { text-align: right; width: 32%; padding-left: 9pt; white-space: nowrap; }
.document .letterhead { width: 100%; border-collapse: collapse; margin: 0; }
.document .letterhead td { padding: 0; vertical-align: top; }
.document .letterhead .header-copy { padding-right: 7mm; }
.document .letterhead .photo-cell { width: 35mm; }
.document .portrait { display: block; width: 35mm; height: 43.75mm; }
.document .photo-rule { border-top: .8pt solid #233e53; height: 0; margin: 12pt 0 14pt; }
.document ul { padding-left: 13pt; margin: 3pt 0 6pt; }
.document li { padding-left: 2pt; margin: 0 0 3.5pt; page-break-inside: avoid; }
.document .force-break { page-break-before: always; }
.document a { color: #233e53; text-decoration: none; }
.document .gap { margin-top: 7pt; }
.letter .subject { font-size: 12pt; line-height: 1.3; font-weight: 700; color: #233e53; margin: 16pt 0 16pt; page-break-after: avoid; }
.letter .address { font-size: 10.5pt; line-height: 1.3; margin-bottom: 1pt; }
.letter .salutation { margin: 17pt 0 11pt; page-break-after: avoid; }
.letter .body { margin-bottom: 11pt; }
.letter .closing { margin: 18pt 0 3pt; page-break-after: avoid; }
.letter .signature { font-weight: 700; margin-bottom: 2pt; }
@media screen and (max-width: 700px) { .sheet { margin: 0; padding: 24px; min-height: 0; box-shadow: none; } }
@media print { body { background: white; } .sheet { margin: 0; padding: 0; box-shadow: none; min-height: 0; max-width: none; } }
"""


def inline_html(text):
    result = []
    for part in inline_parts(text):
        value = html.escape(part["text"])
        if part["bold"]:
            value = f"<strong>{value}</strong>"
        if part["italic"]:
            value = f"<em>{value}</em>"
        if part["url"]:
            value = f'<a href="{html.escape(part["url"], quote=True)}">{value}</a>'
        result.append(value)
    return "".join(result)


def html_document(text, kind="cv", identity=None, stylesheet=None, pdf=False, breaks=None, photo=None):
    blocks = document_blocks(text, kind, identity)
    content = []
    listing = False
    header_end = next((i for i, b in enumerate(blocks) if b["style"] not in ("Name", "Subtitle", "Contact")), len(blocks))
    if photo and header_end:
        content.append('<table class="letterhead"><tr><td class="header-copy">')
    for index, block in enumerate(blocks):
        if photo and header_end and index == header_end:
            content.append(f'</td><td class="photo-cell"><img class="portrait" src="{html.escape(photo, quote=True)}" alt="Applicant portrait"></td></tr></table><div class="photo-rule"></div>')
        if block["style"] == "Bullet":
            if not listing:
                content.append("<ul>")
                listing = True
            content.append(f'<li id="b{index}">{inline_html(block["text"])}</li>')
            continue
        if listing:
            content.append("</ul>")
            listing = False
        style = block["style"].lower()
        classes = style + (" gap" if block["gap"] else "") + (" divider" if block.get("divider") and not photo else "")
        if index in (breaks or set()):
            classes += " force-break"
        tag = {"Name": "h1", "Section": "h2", "Entry": "h3"}.get(block["style"], "p")
        if style == "metadata" and "left_text" in block and len(plain_text(block["right_text"])) <= 32:
            content.append(f'<table id="b{index}" class="entry-meta"><tr><td>{inline_html(block["left_text"])}</td><td class="dates">{inline_html(block["right_text"])}</td></tr></table>')
            continue
        # Uppercase at rendering time too: the PDF renderer has limited CSS support.
        value = block["text"].upper() if block["style"] == "Section" else block["text"]
        content.append(f'<{tag} id="b{index}" class="{classes}">{inline_html(value)}</{tag}>')
    if listing:
        content.append("</ul>")
    if photo and header_end and header_end == len(blocks):
        content.append(f'</td><td class="photo-cell"><img class="portrait" src="{html.escape(photo, quote=True)}" alt="Applicant portrait"></td></tr></table><div class="photo-rule"></div>')
    name = (identity or cv_identity(text)).get("name", "Application")
    css = (f'<link rel="stylesheet" href="{html.escape(stylesheet, quote=True)}">' if stylesheet
           else f"<style>{DOCUMENT_CSS.replace('/fonts/', '') if pdf else DOCUMENT_CSS}</style>")
    article = f'<article class="document {"letter" if kind == "cover_letter" else "cv"}">{"".join(content)}</article>'
    body = article if pdf else f'<main class="sheet">{article}</main>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(name)} · {"Cover letter" if kind == "cover_letter" else "CV"}</title>{css}'
            f'</head><body>{body}</body></html>')


def word_document(text, kind="cv", identity=None, photo=None):
    try:
        from docx import Document
        from docx.enum.style import WD_STYLE_TYPE
        from docx.enum.text import WD_TAB_ALIGNMENT
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Mm, Pt, RGBColor
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
    except ImportError as e:
        raise WorkflowError("Install requirements.txt to enable formatted Word exports.") from e
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(PAGE_MARGIN_MM)
    section.left_margin = section.right_margin = Mm(PAGE_MARGIN_MM)
    section.footer_distance = Mm(9)
    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(11.25 if kind == "cover_letter" else 10.5)
    normal.font.color.rgb = RGBColor.from_string(INK)
    normal.paragraph_format.line_spacing = 1.36 if kind == "cover_letter" else 1.23
    normal.paragraph_format.space_after = Pt(11 if kind == "cover_letter" else 4)
    normal.paragraph_format.widow_control = True
    normal.paragraph_format.keep_together = True

    settings = {
        "Name": (30, NAVY, False, 0, 7, True),
        "Subtitle": (11, NAVY, True, 0, 7, True),
        "Contact": (9.25, MUTED, False, 0, 3, True),
        "Section": (9.5, NAVY, True, 15, 7, True),
        "Entry": (11.25, INK, True, 10, 3, True),
        "Metadata": (9.5, MUTED, False, 0, 6, True),
        "Subject": (12, NAVY, True, 16, 16, True),
        "Address": (10.5, INK, False, 0, 1, True),
        "Salutation": (11, INK, False, 17, 11, True),
        "Closing": (11.25, INK, False, 18, 3, True),
        "Signature": (11, INK, True, 0, 2, False),
    }
    for name, (size, color, bold, before, after, keep) in settings.items():
        style = document.styles.add_style("Application " + name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = normal
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.font.bold = bold
        style.font.name = DISPLAY_FONT if name == "Name" else BODY_FONT
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = keep
        style.paragraph_format.line_spacing = {"Name": 1.12, "Entry": 1.22,
            "Contact": 1.32, "Subject": 1.3, "Address": 1.3}.get(name, 1.23)
        if name == "Section":
            style.font.all_caps = True
            spacing = OxmlElement("w:spacing"); spacing.set(qn("w:val"), "20")
            style.element.get_or_add_rPr().append(spacing)
    for name in ("List Bullet", "List Bullet 2", "List Bullet 3"):
        style = document.styles[name]
        style.base_style = normal
        style.font.name = BODY_FONT
        style.font.size = normal.font.size
        style.paragraph_format.left_indent = Pt(13)
        style.paragraph_format.first_line_indent = Pt(-10)
        style.paragraph_format.space_after = Pt(3.5)
        style.paragraph_format.line_spacing = 1.23
        style.paragraph_format.keep_with_next = False
        style.paragraph_format.keep_together = True

    def border(paragraph, color, size=5):
        borders = OxmlElement("w:pBdr")
        edge = OxmlElement("w:bottom")
        for key, value in {"val": "single", "sz": str(size), "space": "5", "color": color}.items():
            edge.set(qn("w:" + key), value)
        borders.append(edge)
        paragraph._p.get_or_add_pPr().append(borders)

    blocks = document_blocks(text, kind, identity)
    header_end = next((i for i, b in enumerate(blocks) if b["style"] not in ("Name", "Subtitle", "Contact")), len(blocks))
    photo_header = None
    if photo and header_end:
        table = document.add_table(rows=1, cols=2)
        table.autofit = False
        table.columns[0].width = Mm(210 - 2 * PAGE_MARGIN_MM - PHOTO_WIDTH_MM)
        table.columns[1].width = Mm(PHOTO_WIDTH_MM)
        for index, cell in enumerate(table.rows[0].cells):
            cell.width = table.columns[index].width
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            margins = OxmlElement("w:tcMar")
            for side in ("top", "bottom", "left", "right"):
                margin = OxmlElement("w:" + side)
                margin.set(qn("w:w"), str(Mm(7).twips if index == 0 and side == "right" else 0))
                margin.set(qn("w:type"), "dxa"); margins.append(margin)
            cell._tc.get_or_add_tcPr().append(margins)
        picture = table.cell(0, 1).paragraphs[0]
        picture.paragraph_format.space_after = Pt(0)
        picture.add_run().add_picture(io.BytesIO(photo), width=Mm(PHOTO_WIDTH_MM), height=Mm(PHOTO_HEIGHT_MM))
        photo_header = table.cell(0, 0)
    keep_with_next = set()
    for index, block in enumerate(blocks):
        if block["style"] in ("Section", "Entry"):
            keep_with_next.update(range(index, min(len(blocks), heading_lead_end(blocks, index))))
    for index, block in enumerate(blocks):
        style = block["style"]
        if style == "Bullet":
            name = "List Bullet" + (" " + str(block.get("level", 0) + 1) if block.get("level", 0) else "")
        else:
            name = "Normal" if style == "Body" else "Application " + style
        if photo_header is not None and index < header_end:
            paragraph = photo_header.paragraphs[0] if index == 0 else photo_header.add_paragraph()
            paragraph.style = name
        else:
            if photo_header is not None and index == header_end:
                rule = document.add_paragraph()
                rule.paragraph_format.space_after = Pt(14)
                rule.paragraph_format.keep_with_next = True
                border(rule, NAVY, 6)
            paragraph = document.add_paragraph(style=name)
        if index in keep_with_next:
            paragraph.paragraph_format.keep_with_next = True
        if block["gap"] and style in ("Body", "Address", "Signature"):
            paragraph.paragraph_format.space_before = Pt(7)
        if style == "Section":
            border(paragraph, "CAD3DA")
        if block.get("divider") and photo_header is None:
            border(paragraph, NAVY, 6)
            paragraph.paragraph_format.space_after = Pt(14)
        value = block["text"]
        if style == "Metadata" and "left_text" in block and len(plain_text(block["right_text"])) <= 32:
            value = block["left_text"] + "\t" + block["right_text"]
            paragraph.paragraph_format.tab_stops.add_tab_stop(Mm(210 - 2 * PAGE_MARGIN_MM), WD_TAB_ALIGNMENT.RIGHT)
        for part in inline_parts(value):
            if part["url"]:
                link = OxmlElement("w:hyperlink")
                link.set(qn("r:id"), document.part.relate_to(part["url"], RT.HYPERLINK, is_external=True))
                run_xml = OxmlElement("w:r")
                properties = OxmlElement("w:rPr")
                color = OxmlElement("w:color"); color.set(qn("w:val"), NAVY); properties.append(color)
                run_xml.append(properties)
                content = OxmlElement("w:t"); content.text = part["text"]; run_xml.append(content)
                link.append(run_xml); paragraph._p.append(link)
            else:
                run = paragraph.add_run(part["text"])
                if part["bold"]:
                    run.bold = True
                if part["italic"]:
                    run.italic = True

    name = (identity or cv_identity(text)).get("name", "")
    footer = section.footer.paragraphs[0]
    footer_style = document.styles["Footer"]
    footer_style.font.name = BODY_FONT
    footer_style.font.size = Pt(8)
    footer_style.font.color.rgb = RGBColor.from_string(MUTED)
    footer.paragraph_format.keep_together = False
    footer.paragraph_format.space_before = Pt(0)
    footer.paragraph_format.space_after = Pt(0)
    footer.paragraph_format.tab_stops.add_tab_stop(Mm(210 - 2 * PAGE_MARGIN_MM), WD_TAB_ALIGNMENT.RIGHT)
    footer.add_run(name + "\t")
    for field_name, suffix in (("PAGE", " / "), ("NUMPAGES", "")):
        field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), field_name)
        run_xml = OxmlElement("w:r"); content = OxmlElement("w:t"); content.text = "1"
        run_xml.append(content); field.append(run_xml); footer._p.append(field)
        footer.add_run(suffix)
    for run in footer.runs:
        run.font.name = BODY_FONT; run.font.size = Pt(8); run.font.color.rgb = RGBColor.from_string(MUTED)
    update = OxmlElement("w:updateFields"); update.set(qn("w:val"), "true")
    document.settings.element.append(update)
    document.core_properties.title = name + (" — Cover letter" if kind == "cover_letter" else " — CV")
    document.core_properties.author = name
    document.core_properties.subject = "Application document"
    embed_word_fonts(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_document(text, kind="cv", identity=None, photo=None):
    try:
        import pymupdf
    except ImportError as e:
        raise WorkflowError("Install requirements.txt to enable formatted PDF exports.") from e
    page_size = pymupdf.Rect(0, 0, 595.276, 841.89)
    margin = PAGE_MARGIN_MM * 72 / 25.4
    content_rect = pymupdf.Rect(margin, margin, page_size.width - margin, page_size.height - margin)
    archive = pymupdf.Archive(str(FONT_DIR))
    if photo:
        archive.add((photo, "applicant.jpg"))

    def rectfn(index, _filled):
        if index >= 20:
            raise WorkflowError("This document exceeds 20 pages. Shorten the draft before exporting.")
        return page_size, content_rect, None

    blocks = document_blocks(text, kind, identity)
    breaks = set()
    # MuPDF doesn't implement CSS `page-break-after: avoid`. Use actual element
    # positions to move orphaned headings and their dates onto the following page.
    for _attempt in range(10):
        positions = []
        story = pymupdf.Story(html_document(text, kind, identity, pdf=True, breaks=breaks,
                              photo="applicant.jpg" if photo else None), em=10.5, archive=archive)
        document = story.write_with_links(rectfn, positionfn=positions.append)
        pages = {p.id: p.page_num for p in reversed(positions) if p.id and p.open_close & 1}
        required = set()
        for index, block in enumerate(blocks):
            if block["style"] not in ("Section", "Entry"):
                continue
            following = heading_lead_end(blocks, index)
            if following < len(blocks) and pages.get(f"b{index}", -1) < pages.get(f"b{following}", -1):
                required.add(index)
        if not required - breaks:
            break
        breaks.update(required)
        document.close()
    else:
        raise WorkflowError("Document pagination could not settle. Shorten the draft and retry the export.")
    name = (identity or cv_identity(text)).get("name", "")
    for index, page in enumerate(document):
        footer_html = f'<p style="margin:0;font-family:SourceSans;font-size:8pt;color:#61717d">{html.escape(name)}</p>'
        page.insert_htmlbox(pymupdf.Rect(margin, 806, page_size.width - margin - 60, 824),
                            footer_html, css="@font-face {font-family:SourceSans; src:url(SourceSans3-Regular.ttf);}",
                            archive=archive)
        label = f"{index + 1} / {len(document)}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=8)
        page.insert_text((page_size.width - margin - width, 817), label, fontname="helv", fontsize=8, color=(.38, .44, .49))
    document.set_metadata({"title": name + (" — Cover letter" if kind == "cover_letter" else " — CV"), "author": name})
    result = document.tobytes(garbage=4, deflate=True)
    document.close()
    return result
