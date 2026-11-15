"""Bundled, licensed typefaces and standard Word font embedding."""
import uuid
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BODY_FONT = "Source Sans 3"
DISPLAY_FONT = "Source Serif 4"
FONT_FILES = {
    "SourceSans3-Regular.ttf": (BODY_FONT, "embedRegular"),
    "SourceSans3-Bold.ttf": (BODY_FONT, "embedBold"),
    "SourceSans3-It.ttf": (BODY_FONT, "embedItalic"),
    "SourceSans3-BoldIt.ttf": (BODY_FONT, "embedBoldItalic"),
    "SourceSerif4-Regular.ttf": (DISPLAY_FONT, "embedRegular"),
}


def embed_word_fonts(document):
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.opc.oxml import serialize_part_xml

    table = next(rel.target_part for rel in document.part.rels.values() if rel.reltype == RT.FONT_TABLE)
    root = parse_xml(table.blob)
    for filename, (family, variant) in FONT_FILES.items():
        data = bytearray((FONT_DIR / filename).read_bytes())
        key = uuid.uuid4()
        # ECMA-376 font embedding: reversed GUID bytes, repeated over bytes 0–31.
        mask = key.bytes[::-1]
        for index in range(32):
            data[index] ^= mask[index % 16]
        part = Part(PackURI("/word/fonts/" + filename.replace(".ttf", ".odttf")),
                    "application/vnd.openxmlformats-officedocument.obfuscatedFont",
                    bytes(data), document.part.package)
        relation = table.relate_to(part, RT.FONT)
        font = next((node for node in root if node.get(qn("w:name")) == family), None)
        if font is None:
            font = OxmlElement("w:font"); font.set(qn("w:name"), family); root.append(font)
        embedded = OxmlElement("w:" + variant)
        embedded.set(qn("r:id"), relation)
        embedded.set(qn("w:fontKey"), "{" + str(key).upper() + "}")
        embedded.set(qn("w:subsetted"), "false")
        font.append(embedded)
    table._blob = serialize_part_xml(root)
    for name in ("embedTrueTypeFonts", "embedSystemFonts"):
        setting = OxmlElement("w:" + name); setting.set(qn("w:val"), "true")
        document.settings.element.append(setting)
