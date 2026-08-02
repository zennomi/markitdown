from markitdown.converter_utils.docx import pre_process
from markitdown.converter_utils.docx.math import mathtype


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


def test_post_process_markdown_restores_mathtype_latex() -> None:
    assert pre_process.post_process_markdown(r"$x\_y$$z$", {r"x_y"}) == r"$x_y$ $z$"


def test_mathtype_uses_embedded_tex_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        mathtype,
        "_equation_native_stream",
        lambda _: b"TeX Input Language\x00 {x_y} \x00",
    )

    assert mathtype.extract_latex_from_ole(b"OLE") == "x_y"


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
