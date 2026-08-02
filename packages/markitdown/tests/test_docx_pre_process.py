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
