import html
import posixpath
import re
import zipfile
from collections.abc import Mapping
from io import BytesIO
from typing import BinaryIO
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag

from .math.mathtype import extract_latex_from_ole
from .math.omml import OMML_NS, oMath2Latex

MATH_ROOT_TEMPLATE = "".join(
    (
        "<w:document ",
        'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" ',
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" ',
        'xmlns:o="urn:schemas-microsoft-com:office:office" ',
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ',
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ',
        'xmlns:v="urn:schemas-microsoft-com:vml" ',
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" ',
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" ',
        'xmlns:w10="urn:schemas-microsoft-com:office:word" ',
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ',
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" ',
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" ',
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" ',
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" ',
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14">',
        "{0}</w:document>",
    )
)


def _convert_omath_to_latex(tag: Tag) -> str:
    """
    Converts an OMML (Office Math Markup Language) tag to LaTeX format.

    Args:
        tag (Tag): A BeautifulSoup Tag object representing the OMML element.

    Returns:
        str: The LaTeX representation of the OMML element.
    """
    # Format the tag into a complete XML document string
    math_root = ET.fromstring(MATH_ROOT_TEMPLATE.format(str(tag)))
    # Find the 'oMath' element within the XML document
    math_element = math_root.find(OMML_NS + "oMath")
    if math_element is None:
        return "[Equation]"
    try:
        return oMath2Latex(math_element).latex
    except Exception:
        return "[Equation]"


def _get_omath_tag_replacement(tag: Tag, block: bool = False) -> Tag:
    """
    Creates a replacement tag for an OMML (Office Math Markup Language) element.

    Args:
        tag (Tag): A BeautifulSoup Tag object representing the "oMath" element.
        block (bool, optional): If True, the LaTeX will be wrapped in double dollar signs for block mode. Defaults to False.

    Returns:
        Tag: A BeautifulSoup Tag object representing the replacement element.
    """
    t_tag = Tag(name="w:t")
    t_tag.string = (
        f"$${_convert_omath_to_latex(tag)}$$"
        if block
        else f"${_convert_omath_to_latex(tag)}$"
    )
    r_tag = Tag(name="w:r")
    r_tag.append(t_tag)
    return r_tag


def _replace_equations(tag: Tag):
    """
    Replaces OMML (Office Math Markup Language) elements with their LaTeX equivalents.

    Args:
        tag (Tag): A BeautifulSoup Tag object representing the OMML element. Could be either "oMathPara" or "oMath".

    Raises:
        ValueError: If the tag is not supported.
    """
    if tag.name == "oMathPara":
        # Create a new paragraph tag
        p_tag = Tag(name="w:p")
        # Replace each 'oMath' child tag with its LaTeX equivalent as block equations
        for child_tag in tag.find_all("oMath"):
            p_tag.append(_get_omath_tag_replacement(child_tag, block=True))
        # Replace the original 'oMathPara' tag with the new paragraph tag
        tag.replace_with(p_tag)
    elif tag.name == "oMath":
        # Replace the 'oMath' tag with its LaTeX equivalent as inline equation
        tag.replace_with(_get_omath_tag_replacement(tag, block=False))
    else:
        raise ValueError(f"Not supported tag: {tag.name}")


def _pre_process_math(content: bytes) -> bytes:
    """
    Pre-processes the math content in a DOCX -> XML file by converting OMML (Office Math Markup Language) elements to LaTeX.
    This preprocessed content can be directly replaced in the DOCX file -> XMLs.

    Args:
        content (bytes): The XML content of the DOCX file as bytes.

    Returns:
        bytes: The processed content with OMML elements replaced by their LaTeX equivalents, encoded as bytes.
    """
    soup = BeautifulSoup(content.decode(), features="xml")
    for tag in soup.find_all("oMathPara"):
        _replace_equations(tag)
    for tag in soup.find_all("oMath"):
        _replace_equations(tag)
    return str(soup).encode()


class _PreprocessedDocx(BytesIO):
    """A preprocessed DOCX stream and the MathType expressions inserted into it."""

    generated_tex: set[str]

    def __init__(self) -> None:
        super().__init__()
        self.generated_tex = set()


def _relationship_target(source_part: str, target: str) -> str:
    """Resolve a relationship target relative to its DOCX package part."""
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(source_part), target)
    ).lstrip("/")


def _pre_process_mathtype(
    files: Mapping[str, bytes], source_part: str = "word/document.xml"
) -> tuple[bytes | None, set[str]]:
    """Replace MathType OLE objects in a DOCX XML part with inline LaTeX."""
    relationships_part = (
        f"{posixpath.dirname(source_part)}/_rels/{posixpath.basename(source_part)}.rels"
    )
    if source_part not in files or relationships_part not in files:
        return None, set()

    try:
        relationships_root = ET.fromstring(files[relationships_part])
    except ET.ParseError:
        return None, set()

    rid_to_target = {
        relationship.attrib["Id"]: _relationship_target(
            source_part, relationship.attrib["Target"]
        )
        for relationship in relationships_root
        if relationship.attrib.get("Id") and relationship.attrib.get("Target")
    }
    target_to_latex = {
        target: tex
        for target in set(rid_to_target.values())
        if target.startswith("word/embeddings/")
        and target in files
        and (tex := extract_latex_from_ole(files[target])) is not None
    }
    if not target_to_latex:
        return None, set()

    try:
        xml_content = files[source_part].decode("utf-8")
    except UnicodeDecodeError:
        return None, set()

    generated_tex: set[str] = set()

    def replace_object(match: re.Match[str]) -> str:
        for relationship_id in re.findall(
            r"""r:(?:id|embed|link)=["']([^"']+)["']""", match.group(0)
        ):
            tex = target_to_latex.get(rid_to_target.get(relationship_id, ""))
            if tex is not None:
                if not tex:
                    return ""
                generated_tex.add(tex)
                return f'<w:t xml:space="preserve">${html.escape(tex)}$</w:t>'
        return match.group(0)

    updated_xml = re.sub(
        r"<w:object\b.*?</w:object>", replace_object, xml_content, flags=re.DOTALL
    )
    return updated_xml.encode("utf-8"), generated_tex


def post_process_markdown(markdown: str, generated_tex: set[str]) -> str:
    """Restore the MathType LaTeX that the Markdown converter escaped."""
    for tex in generated_tex:
        escaped_tex = re.sub(r"([_*\[\]`])", r"\\\1", tex)
        markdown = markdown.replace(f"${escaped_tex}$", f"${tex}$")
        markdown = markdown.replace(f"${tex}$$", f"${tex}$ $")
    return markdown


def pre_process_docx(input_docx: BinaryIO) -> _PreprocessedDocx:
    """Pre-process OMML and MathType equations in a DOCX file in memory."""
    output_docx = _PreprocessedDocx()
    with zipfile.ZipFile(input_docx, mode="r") as zip_input:
        files = {name: zip_input.read(name) for name in zip_input.namelist()}
        # Each Word story has its own relationship part. Process every story
        # that Mammoth can read, not just the document body.
        pre_process_enable_files = [
            name
            for name in files
            if name
            in {
                "word/document.xml",
                "word/footnotes.xml",
                "word/endnotes.xml",
                "word/comments.xml",
            }
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        ]
        for source_part in pre_process_enable_files:
            mathtype_content, generated_tex = _pre_process_mathtype(files, source_part)
            if mathtype_content is not None:
                files[source_part] = mathtype_content
            output_docx.generated_tex.update(generated_tex)

        with zipfile.ZipFile(output_docx, mode="w") as zip_output:
            zip_output.comment = zip_input.comment
            for name, content in files.items():
                if name in pre_process_enable_files:
                    try:
                        zip_output.writestr(name, _pre_process_math(content))
                    except Exception:
                        # Preserve the original DOCX part if its XML cannot be processed.
                        zip_output.writestr(name, content)
                else:
                    zip_output.writestr(name, content)
    output_docx.seek(0)
    return output_docx
