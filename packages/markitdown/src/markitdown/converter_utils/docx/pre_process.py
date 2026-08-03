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

WORDPROCESSINGML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{WORDPROCESSINGML_NS}}}"

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


def _word_tag(name: str) -> str:
    return f"{W}{name}"


def _read_numbering_level(level: ET.Element) -> dict[str, str | int]:
    """Extract the label format from a ``w:lvl`` element."""
    num_fmt = level.find(_word_tag("numFmt"))
    level_text = level.find(_word_tag("lvlText"))
    start = level.find(_word_tag("start"))
    try:
        start_value = int(start.get(_word_tag("val"), "1")) if start is not None else 1
    except ValueError:
        start_value = 1
    return {
        "format": num_fmt.get(_word_tag("val"), "decimal")
        if num_fmt is not None
        else "decimal",
        "text": level_text.get(_word_tag("val"), "") if level_text is not None else "",
        "start": start_value,
    }


def _numbering_level_definitions(
    files: Mapping[str, bytes],
) -> dict[str, dict[int, dict[str, str | int]]]:
    """Read the numbering formats for each concrete Word numbering instance."""
    numbering_xml = files.get("word/numbering.xml")
    if numbering_xml is None:
        return {}

    try:
        root = ET.fromstring(numbering_xml)
    except ET.ParseError:
        return {}

    abstract_levels: dict[str, dict[int, dict[str, str | int]]] = {}
    for abstract_num in root.findall(_word_tag("abstractNum")):
        abstract_num_id = abstract_num.get(_word_tag("abstractNumId"))
        if abstract_num_id is None:
            continue
        levels: dict[int, dict[str, str | int]] = {}
        for level in abstract_num.findall(_word_tag("lvl")):
            level_index = level.get(_word_tag("ilvl"))
            if level_index is None:
                continue
            try:
                index = int(level_index)
            except ValueError:
                continue
            levels[index] = _read_numbering_level(level)
        abstract_levels[abstract_num_id] = levels

    definitions: dict[str, dict[int, dict[str, str | int]]] = {}
    for num in root.findall(_word_tag("num")):
        num_id = num.get(_word_tag("numId"))
        abstract_num_id_element = num.find(_word_tag("abstractNumId"))
        if num_id is None or abstract_num_id_element is None:
            continue
        abstract_num_id = abstract_num_id_element.get(_word_tag("val"))
        if abstract_num_id is None or abstract_num_id not in abstract_levels:
            continue
        levels = {
            index: value.copy()
            for index, value in abstract_levels[abstract_num_id].items()
        }
        for override in num.findall(_word_tag("lvlOverride")):
            override_index = override.get(_word_tag("ilvl"))
            if override_index is None:
                continue
            try:
                index = int(override_index)
            except ValueError:
                continue
            override_level = override.find(_word_tag("lvl"))
            if override_level is not None:
                levels[index] = _read_numbering_level(override_level)
            start_override = override.find(_word_tag("startOverride"))
            if start_override is not None and index in levels:
                try:
                    levels[index]["start"] = int(
                        start_override.get(_word_tag("val"), "1")
                    )
                except ValueError:
                    pass
        definitions[num_id] = levels
    return definitions


def _format_number(value: int, number_format: str) -> str:
    if number_format == "decimalZero":
        return f"{value:02d}"
    if number_format in {"lowerLetter", "upperLetter"}:
        result = ""
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            result = chr(ord("a") + remainder) + result
        return result.upper() if number_format == "upperLetter" else result
    if number_format in {"lowerRoman", "upperRoman"}:
        numerals = (
            (1000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        )
        result = ""
        for amount, numeral in numerals:
            count, value = divmod(value, amount)
            result += numeral * count
        return result.lower() if number_format == "lowerRoman" else result
    return str(value)


def _render_numbering_label(
    level_text: str,
    levels: dict[int, dict[str, str | int]],
    counters: dict[int, int],
) -> str:
    def replace_placeholder(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        definition = levels.get(index)
        if definition is None:
            return match.group(0)
        value = counters.get(index, int(definition["start"]))
        return _format_number(value, str(definition["format"]))

    return re.sub(r"%([1-9])", replace_placeholder, level_text)


def _is_standard_markdown_numbering(level_index: int, level_text: str) -> bool:
    """Whether Mammoth's ordinary ordered-list output preserves this label."""
    return level_text == f"%{level_index + 1}."


def _pre_process_numbering_labels(
    files: Mapping[str, bytes], source_part: str
) -> bytes | None:
    """Materialize custom Word list labels as paragraph text before Mammoth runs."""
    if source_part not in files:
        return None
    definitions = _numbering_level_definitions(files)
    if not definitions:
        return None
    try:
        root = ET.fromstring(files[source_part])
    except ET.ParseError:
        return None

    counters_by_num_id: dict[str, dict[int, int]] = {}
    changed = False
    for paragraph in root.iter(_word_tag("p")):
        properties = paragraph.find(_word_tag("pPr"))
        if properties is None:
            continue
        num_properties = properties.find(_word_tag("numPr"))
        if num_properties is None:
            continue
        num_id_element = num_properties.find(_word_tag("numId"))
        level_element = num_properties.find(_word_tag("ilvl"))
        if num_id_element is None:
            continue
        num_id = num_id_element.get(_word_tag("val"))
        try:
            level_index = (
                int(level_element.get(_word_tag("val"), "0"))
                if level_element is not None
                else 0
            )
        except ValueError:
            continue
        levels = definitions.get(num_id or "")
        if levels is None or level_index not in levels:
            continue

        level = levels[level_index]
        if level["format"] == "bullet":
            continue
        counters = counters_by_num_id.setdefault(num_id or "", {})
        counters[level_index] = counters.get(level_index, int(level["start"]) - 1) + 1
        for deeper_level in tuple(counters):
            if deeper_level > level_index:
                del counters[deeper_level]

        level_text = str(level["text"])
        if _is_standard_markdown_numbering(level_index, level_text):
            continue
        label = _render_numbering_label(level_text, levels, counters)
        if not label:
            continue
        if not label[-1].isspace():
            label += " "

        properties.remove(num_properties)
        run = ET.Element(_word_tag("r"))
        text = ET.SubElement(run, _word_tag("t"))
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = label
        paragraph.insert(list(paragraph).index(properties) + 1, run)
        changed = True

    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True) if changed else None
    )


def post_process_markdown(markdown: str, generated_tex: set[str]) -> str:
    """Restore the MathType LaTeX that the Markdown converter escaped."""
    for tex in generated_tex:
        escaped_tex = re.sub(r"([_*\[\]`])", r"\\\1", tex)
        markdown = markdown.replace(f"${escaped_tex}$", f"${tex}$")
        markdown = markdown.replace(f"${tex}$$", f"${tex}$ $")
    return markdown


def pre_process_docx(
    input_docx: BinaryIO, *, preserve_docx_numbering: bool = False
) -> _PreprocessedDocx:
    """Pre-process DOCX math and, optionally, custom Word numbering labels."""
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
            try:
                # ElementTree, used for numbering labels below, renames XML prefixes.
                # Convert OMML first because its converter expects the math prefix in
                # the original Word XML.
                files[source_part] = _pre_process_math(files[source_part])
            except Exception:
                # Preserve the original DOCX part if its XML cannot be processed.
                pass
            if preserve_docx_numbering:
                numbered_content = _pre_process_numbering_labels(files, source_part)
                if numbered_content is not None:
                    files[source_part] = numbered_content

        with zipfile.ZipFile(output_docx, mode="w") as zip_output:
            zip_output.comment = zip_input.comment
            for name, content in files.items():
                zip_output.writestr(name, content)
    output_docx.seek(0)
    return output_docx
