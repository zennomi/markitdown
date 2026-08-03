import zipfile
from io import BytesIO

import pytest

from markitdown import MarkItDown, StreamInfo
from markitdown.converter_utils.docx import pre_process
from markitdown.converter_utils.docx.math import mathtype


def _custom_numbered_docx() -> BytesIO:
    """Build a minimal DOCX with custom and ordinary numbered-list formats."""
    document_xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:body>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>First question</w:t></w:r></w:p>
  <m:oMathPara><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></m:oMathPara>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Second question</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>Ordinary item</w:t></w:r></w:p>
  <w:sectPr/>
</w:body></w:document>'''
    numbering_xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="C\xc3\xa2u %1:"/></w:lvl></w:abstractNum>
  <w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>'''
    package_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>'''
    docx = BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_relationships)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/numbering.xml", numbering_xml)
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
    docx.seek(0)
    return docx


def test_docx_preserves_custom_numbering_labels_when_requested() -> None:
    markitdown = MarkItDown()
    default_result = markitdown.convert_stream(
        _custom_numbered_docx(), stream_info=StreamInfo(extension=".docx")
    )
    assert "1. First question" in default_result.text_content
    assert "Câu" not in default_result.text_content

    result = markitdown.convert_stream(
        _custom_numbered_docx(),
        stream_info=StreamInfo(extension=".docx"),
        preserve_docx_numbering=True,
    )

    assert "Câu 1: First question" in result.text_content
    assert "Câu 2: Second question" in result.text_content
    assert "$$x$$" in result.text_content
    assert "1. Ordinary item" in result.text_content


def test_pre_process_mathtype_replaces_only_embedded_ole_objects(monkeypatch) -> None:
    extracted = []

    def extract_latex(ole_bytes: bytes) -> str | None:
        extracted.append(ole_bytes)
        return r"x_y" if ole_bytes == b"equation" else None

    monkeypatch.setattr(pre_process, "extract_latex_from_ole", extract_latex)
    files = {
        "word/document.xml": (
            b'<w:document xmlns:w="urn:w" xmlns:r="urn:r">'
            b'<w:object><o:OLEObject r:id="rIdEquation"/></w:object>'
            b'<w:object><o:OLEObject r:id="rIdImage"/></w:object>'
            b"</w:document>"
        ),
        "word/_rels/document.xml.rels": (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rIdEquation" Target="embeddings/equation.bin"/>'
            b'<Relationship Id="rIdImage" Target="media/preview.wmf"/>'
            b"</Relationships>"
        ),
        "word/embeddings/equation.bin": b"equation",
        "word/media/preview.wmf": b"not an OLE equation",
    }

    updated, generated_tex = pre_process._pre_process_mathtype(files)

    assert extracted == [b"equation"]
    assert generated_tex == {r"x_y"}
    assert updated is not None
    assert b'<w:t xml:space="preserve">$x_y$</w:t>' in updated
    assert b"rIdImage" in updated


def test_pre_process_mathtype_removes_empty_ole_equations(monkeypatch) -> None:
    monkeypatch.setattr(pre_process, "extract_latex_from_ole", lambda _: "")
    files = {
        "word/document.xml": b'<w:object><o:OLEObject r:id="rIdEquation"/></w:object>',
        "word/_rels/document.xml.rels": (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rIdEquation" Target="embeddings/equation.bin"/>'
            b"</Relationships>"
        ),
        "word/embeddings/equation.bin": b"empty equation",
    }

    updated, generated_tex = pre_process._pre_process_mathtype(files)

    assert updated is not None
    assert b"w:object" not in updated
    assert generated_tex == set()


def test_post_process_markdown_restores_mathtype_latex() -> None:
    assert pre_process.post_process_markdown(r"$x\_y$$z$", {r"x_y"}) == r"$x_y$ $z$"


def test_mathtype_uses_embedded_tex_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        mathtype,
        "_equation_native_stream",
        lambda _: b"TeX Input Language\x00 {x_y} \x00",
    )

    assert mathtype.extract_latex_from_ole(b"OLE") == "x_y"


def test_mathtype_renders_mtextra_spacing_character() -> None:
    assert mathtype._tex_character(0xEF05, 139) == r"\quad "


def test_mathtype_renders_vector_and_fence_templates(monkeypatch) -> None:
    def char(codepoint: int) -> bytes:
        return b"\x02\x00\x83" + codepoint.to_bytes(2, "little")

    def line(contents: bytes) -> bytes:
        return b"\x01\x00" + contents + b"\x00"

    def template(selector: int, variation: int, contents: bytes) -> bytes:
        return b"\x03\x00" + bytes((selector, variation, 0)) + contents + b"\x00"

    mtef_header = b"\x05\x01\x00\x06\x00DSMT6\x00\x01"
    vector = template(31, 2, line(char(ord("v"))) + char(0x20D7))
    brackets = template(3, 3, line(char(ord("x"))) + char(ord("[")) + char(ord("]")))
    bars = template(4, 3, line(char(ord("y"))) + char(0xEC07) + char(0xEC08))
    payload = mtef_header + line(vector + brackets + bars)
    native = bytearray(28)
    native[:2] = (28).to_bytes(2, "little")
    native[8:12] = len(payload).to_bytes(4, "little")
    native.extend(payload)
    monkeypatch.setattr(mathtype, "_equation_native_stream", lambda _: bytes(native))

    assert (
        mathtype.extract_latex_from_ole(b"OLE")
        == r"\vec{v}\left[{x}\right]\left|{y}\right|"
    )


def _template(selector: int, variation: int = 0, *contents: str) -> dict:
    return {
        "kind": "template",
        "selector": selector,
        "variation": variation,
        "children": [
            {
                "kind": "line",
                "children": [{"kind": "char", "mtcode": ord(text), "typeface": 131}],
            }
            for text in contents
        ],
    }


@pytest.mark.parametrize(
    ("selector", "variation", "expected"),
    [
        (0, 3, r"\left\langle{x}\right\rangle"),
        (2, 3, r"\left\{{x}\right\}"),
        (5, 3, r"\left\|{x}\right\|"),
        (6, 3, r"\left\lfloor{x}\right\rfloor"),
        (7, 3, r"\left\lceil{x}\right\rceil"),
        (9, 0x23, r"\left]{x}\right["),
        (11, 2, r"{x}/{y}"),
        (12, 1, r"\underline{\underline{x}}"),
        (13, 0, r"\overline{x}"),
        (14, 0x28, r"\underset{\rightarrow}{x}"),
        (16, 1, r"\sum_{z}^{y}x"),
        (19, 0, r"\bigcup x"),
        (23, 0, r"\lim_{y}^{z}x"),
        (24, 1, r"\overbrace{x}^{y}"),
        (25, 0, r"\underbracket{x}_{y}"),
        (26, 0, r"\overline{x})y"),
        (27, 0, r"_{x}^{y}"),
        (30, 3, r"\langlex|y\rangle"),
        (31, 4, r"\underset{\rightarrow}{x}"),
        (32, 0, r"\widetilde{x}"),
        (33, 0, r"\widehat{x}"),
        (34, 0, r"\overset{\frown}{x}"),
        (35, 0, r"\overset{\smile}{x}"),
        (36, 1, r"\cancel{x}"),
        (37, 0, r"\boxed{x}"),
    ],
)
def test_mathtype_renders_documented_v5_templates(
    selector: int, variation: int, expected: str
) -> None:
    if selector in {16, 23}:
        contents = ("x", "y", "z")
    elif selector in {14, 19}:
        contents = ("x",)
    else:
        contents = ("x", "y")
    assert mathtype._render_mtef(_template(selector, variation, *contents)) == expected


@pytest.mark.parametrize(
    ("variation", "expected"),
    [
        (1, r"\left\{{x}\right."),
        (2, r"\left.{x}\right\}"),
    ],
)
def test_mathtype_pairs_one_sided_fences_with_null_delimiters(
    variation: int, expected: str
) -> None:
    assert mathtype._render_mtef(_template(2, variation, "x")) == expected


def test_mathtype_renders_piles_inside_an_array() -> None:
    pile = {
        "kind": "pile",
        "children": [
            {"kind": "line", "children": [{"kind": "char", "mtcode": ord("x"), "typeface": 131}]},
            {"kind": "line", "children": [{"kind": "char", "mtcode": ord("y"), "typeface": 131}]},
        ],
    }

    assert mathtype._render_mtef(pile) == r"\begin{array}{c}x\\y\end{array}"


def test_mathtype_supports_every_documented_v5_template_selector() -> None:
    for selector in range(38):
        assert mathtype._render_mtef(_template(selector, 0, "x", "y", "z", "∑"))


def test_mathtype_parses_legacy_v3_and_v4_records() -> None:
    def char(value: str) -> bytes:
        return b"\x02\x83" + ord(value).to_bytes(2, "little")

    def line(contents: bytes) -> bytes:
        return b"\x01" + contents + b"\x00"

    def template(selector: int, variation: int, contents: bytes) -> bytes:
        return b"\x03" + bytes((selector, variation, 0)) + contents + b"\x00"

    fraction = template(14, 0, line(char("x")) + line(char("y")))
    script = template(15, 2, line(char("i")) + line(char("j")))
    for version, equation, expected in (
        (3, fraction, r"\frac{x}{y}"),
        (4, script, r"_{i}^{j}"),
    ):
        payload = bytes((version, 1, 0, 3, 0)) + line(equation)
        tree = mathtype._build_mtef_tree(mathtype._read_mtef_nodes(payload))
        assert mathtype._render_mtef(tree) == expected


def test_mathtype_uses_font_position_when_mtcode_is_omitted() -> None:
    header = b"\x05\x01\x00\x06\x00DSMT6\x00\x01"
    # CHAR options 0x24: no MTCode, followed by one byte of font position.
    payload = header + b"\x01\x00\x02\x24\x83x\x00"

    tree = mathtype._build_mtef_tree(mathtype._read_mtef_nodes(payload))

    assert mathtype._render_mtef(tree) == "x"


def test_pre_process_docx_handles_mathtype_in_each_word_story(monkeypatch) -> None:
    monkeypatch.setattr(pre_process, "extract_latex_from_ole", lambda _: "x")
    object_xml = b'<w:object><o:OLEObject r:id="rIdEquation"/></w:object>'
    relationships = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rIdEquation" Target="embeddings/equation.bin"/>'
        b"</Relationships>"
    )
    input_docx = BytesIO()
    with zipfile.ZipFile(input_docx, "w") as archive:
        for part in ("word/document.xml", "word/footnotes.xml", "word/header1.xml"):
            archive.writestr(part, object_xml)
            directory, filename = part.rsplit("/", 1)
            archive.writestr(f"{directory}/_rels/{filename}.rels", relationships)
        archive.writestr("word/embeddings/equation.bin", b"equation")
    input_docx.seek(0)

    output_docx = pre_process.pre_process_docx(input_docx)

    assert output_docx.generated_tex == {"x"}
    with zipfile.ZipFile(output_docx) as archive:
        for part in ("word/document.xml", "word/footnotes.xml", "word/header1.xml"):
            assert b"w:object" not in archive.read(part)


def test_mathtype_parses_nudged_ruler_and_matrix_records() -> None:
    def char(value: str) -> bytes:
        return b"\x02\x00\x83" + ord(value).to_bytes(2, "little")

    def line(contents: bytes, options: int = 0) -> bytes:
        return b"\x01" + bytes((options,)) + contents + b"\x00"

    header = b"\x05\x01\x00\x06\x00DSMT6\x00\x01"
    ruler = b"\x07\x01\x00\x00\x00"
    nudged_line = line(b"\x81\x81" + ruler + char("x"), options=0x0A)
    matrix = (
        b"\x05\x00\x00\x00\x00\x02\x02\x00\x00"
        + b"".join(line(char(value)) for value in "abcd")
        + b"\x00"
    )
    definitions = (
        b"\x13WinAllBasicCodePages\x00"  # encoding definition
        b"\x11\x05Times New Roman\x00"  # font definition
        b"\x08\x01\x02"  # font style definition
        b"\x09\x64\x00\x00\x00"  # extended size record
        b"\x0f\x01"  # color reference
        b"\x10\x00\x00\x00\x00\x00\x00\x00"  # RGB color definition
        b"\x64\xff\x02\x00OK"  # future record with a three-byte length
    )
    nodes = mathtype._read_mtef_nodes(header + definitions + line(nudged_line + matrix))
    rendered = mathtype._render_mtef(mathtype._build_mtef_tree(nodes))

    assert rendered == r"x\begin{array}{cc}a & b\\c & d\end{array}"
