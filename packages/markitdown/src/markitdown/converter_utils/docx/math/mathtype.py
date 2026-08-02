"""Extract LaTeX source from MathType Equation Native OLE streams."""

from __future__ import annotations

import io


def _get_olefile():
    """Load the optional OLE parser only when a MathType object is encountered."""
    try:
        import olefile
    except ImportError:
        return None
    return olefile


# MTEF record types used by MathType 4+ Equation Native streams.
_MTEF_END = 0
_MTEF_LINE = 1
_MTEF_CHAR = 2
_MTEF_TMPL = 3
_MTEF_PILE = 4
_MTEF_MATRIX = 5
_MTEF_EMBELL = 6
_MTEF_FONT_STYLE_DEF = 8
_MTEF_SIZE = 9
_MTEF_FULL = 10
_MTEF_SUB = 11
_MTEF_SUB2 = 12
_MTEF_SYM = 13
_MTEF_SUBSYM = 14
_MTEF_COLOR = 15
_MTEF_COLOR_DEF = 16
_MTEF_FONT_DEF = 17
_MTEF_EQN_PREFS = 18
_MTEF_ENCODING_DEF = 19
_MTEF_FUTURE = 100

# MathType selectors used to describe structured equation constructs.
_TMPL_PAREN = 1
_TMPL_BRACE = 2
_TMPL_BRACK = 3
_TMPL_BAR = 4
_TMPL_ROOT = 10
_TMPL_FRACTION = 11
_TMPL_SUB = 27
_TMPL_SUP = 28
_TMPL_SUBSUP = 29
_TMPL_VECTOR = 31
_TMPL_HAT = 33

# MathType stores Unicode MTCode values, not Symbol-font byte values.  Mapping
# these explicitly keeps the generated source portable across TeX renderers.
_TEX_CHARS = {
    0x00A0: "~",
    0x00B0: r"^\circ ",
    0x00B1: r"\pm ",
    0x0302: r"\widehat ",
    0x0394: r"\Delta ",
    0x03C0: r"\pi ",
    0x2115: r"\mathbb{N}",
    0x21D2: r"\Rightarrow ",
    0x21D4: r"\Leftrightarrow ",
    0x2208: r"\in ",
    0x2212: "-",
    0x2229: r"\cap ",
    0x22A5: r"\bot ",
    0x22C5: r"\cdot ",
    0x22EE: r"\vdots ",
    0x223C: r"\sim ",
    0xEF02: r"\,",
}


class MTEFParseError(ValueError):
    """The Equation Native stream is not a complete MTEF equation."""


class MTEFReader:
    """Strict, little-endian reader for the MTEF v3-v5 record stream."""

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_u8(self):
        if self.pos >= len(self.data):
            raise MTEFParseError("unexpected end of MTEF data")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def read_u16(self):
        if self.pos + 2 > len(self.data):
            raise MTEFParseError("unexpected end of MTEF data")
        value = int.from_bytes(self.data[self.pos : self.pos + 2], "little")
        self.pos += 2
        return value

    def read_c_string(self):
        end = self.data.find(b"\x00", self.pos)
        if end == -1:
            raise MTEFParseError("unterminated MTEF string")
        value = self.data[self.pos : end]
        self.pos = end + 1
        return value

    def skip(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise MTEFParseError("truncated MTEF record")
        self.pos += size


def _node(kind, **attributes):
    return {"kind": kind, "children": [], **attributes}


def _skip_nudge(reader):
    # A compact nudge has two 16-bit values.  0x80 is the extended marker.
    x = reader.read_u16()
    y = reader.read_u16()
    if x == 0x80 or y == 0x80:
        reader.skip(4)


def _skip_dimension_array(reader, count):
    # Equation preferences encode dimensions as nibbles terminated by 0xF.
    completed = 0
    while completed < count:
        value = reader.read_u8()
        completed += (value >> 4 == 0xF) + ((value & 0x0F) == 0xF)


def _skip_equation_preferences(reader):
    reader.read_u8()  # options
    _skip_dimension_array(reader, reader.read_u8())  # sizes
    _skip_dimension_array(reader, reader.read_u8())  # spaces
    for _ in range(reader.read_u8()):
        if reader.read_u8() != 0:
            reader.read_u8()


def _read_mtef_nodes(data):
    reader = MTEFReader(data)
    version = reader.read_u8()
    if version not in (3, 4, 5):
        raise MTEFParseError(f"unsupported MTEF version {version}")

    # platform, product, version, version sub, application, inline flag
    reader.skip(4)
    reader.read_c_string()
    reader.read_u8()

    nodes = []
    while reader.pos < len(reader.data):
        record = reader.read_u8()
        if record >= _MTEF_FUTURE:
            reader.skip(reader.read_u8())
        elif record == _MTEF_END:
            nodes.append(_node("end"))
        elif record == _MTEF_LINE:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            if options & 0x04:
                reader.read_u8()
            if options & 0x02:
                for _ in range(reader.read_u8()):
                    reader.skip(3)
            nodes.append(_node("line", null=bool(options & 0x01)))
        elif record == _MTEF_CHAR:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            typeface = reader.read_u8()
            mtcode = None if options & 0x20 else reader.read_u16()
            if options & 0x04:
                reader.read_u8()
            if options & 0x10:
                reader.read_u16()
            if mtcode is None:
                raise MTEFParseError("character record has no MTCode")
            nodes.append(_node("char", mtcode=mtcode, typeface=typeface))
        elif record == _MTEF_TMPL:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            selector = reader.read_u8()
            variation = reader.read_u8()
            if variation & 0x80:
                variation = (variation & 0x7F) | (reader.read_u8() << 8)
            reader.read_u8()  # template options
            nodes.append(_node("template", selector=selector, variation=variation))
        elif record == _MTEF_PILE:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            reader.skip(2)  # horizontal and vertical alignment
            nodes.append(_node("pile"))
        elif record == _MTEF_MATRIX:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            reader.skip(3)  # vertical, horizontal, and vertical justification
            rows = reader.read_u8()
            columns = reader.read_u8()
            nodes.append(_node("matrix", rows=rows, columns=columns))
            # MTEF represents matrix row/column partitions with two empty lines.
            nodes.extend((_node("line", null=False), _node("line", null=False)))
        elif record == _MTEF_EMBELL:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            nodes.append(_node("embellishment", embellishment=reader.read_u8()))
        elif record in (_MTEF_FONT_STYLE_DEF, _MTEF_FONT_DEF):
            reader.read_u8()
            reader.read_c_string()
        elif record == _MTEF_EQN_PREFS:
            _skip_equation_preferences(reader)
        elif record == _MTEF_ENCODING_DEF:
            reader.read_c_string()
        elif record == _MTEF_SIZE:
            reader.skip(2)
        elif record == _MTEF_COLOR:
            reader.read_u8()
        elif record == _MTEF_COLOR_DEF:
            options = reader.read_u8()
            reader.skip(8 if options & 0x01 else 6)
            if options & 0x04:
                reader.read_c_string()
        elif record in (_MTEF_FULL, _MTEF_SUB, _MTEF_SUB2, _MTEF_SYM, _MTEF_SUBSYM):
            # These records alter formatting only; they have no payload.
            pass
        else:
            raise MTEFParseError(f"unsupported MTEF record {record}")
    return nodes


def _build_mtef_tree(nodes):
    root = _node("root")
    stack = [root]
    for current in nodes:
        kind = current["kind"]
        if kind in ("line", "template", "pile", "matrix"):
            if not stack:
                raise MTEFParseError("MTEF container has no parent")
            stack[-1]["children"].append(current)
            if kind != "line" or not current["null"]:
                stack.append(current)
        elif kind == "char":
            if not stack:
                raise MTEFParseError("MTEF character has no parent")
            stack[-1]["children"].append(current)
        elif kind == "embellishment":
            if not stack:
                raise MTEFParseError("MTEF embellishment has no parent")
            children = stack[-1]["children"]
            children.append(current)
            # These accents precede their character in TeX but follow it in MTEF.
            if current["embellishment"] in (2, 9, 17) and len(children) >= 2:
                children[-2], children[-1] = children[-1], children[-2]
            stack.append(current)
        elif kind == "end":
            if stack:
                stack.pop()
    return root


def _tex_character(mtcode, typeface):
    if mtcode in _TEX_CHARS:
        return _TEX_CHARS[mtcode]
    try:
        character = chr(mtcode)
    except ValueError as exc:
        raise MTEFParseError(f"invalid MTCode {mtcode}") from exc

    if character in r"#$%&~_^\\{}":
        character = "\\" + character
    # fnTEXT uses upright letters (for example, units such as cm).
    if typeface - 128 == 1 and character.strip():
        return rf"\mathrm{{{character}}}"
    return character


def _render_mtef(node):
    kind = node["kind"]
    if kind == "root" or kind == "line":
        return "".join(_render_mtef(child) for child in node["children"])
    if kind == "char":
        return _tex_character(node["mtcode"], node["typeface"])
    if kind == "embellishment":
        marks = {2: r"\dot ", 5: "'", 6: "''", 18: "'''", 9: r"\hat ", 17: r"\bar "}
        return marks.get(node["embellishment"], "")
    if kind == "template":
        slots = [_render_mtef(child) for child in node["children"]]
        slot = lambda index: slots[index] if index < len(slots) else ""
        selector = node["selector"]
        if selector == _TMPL_FRACTION:
            return rf"\frac{{{slot(0)}}}{{{slot(1)}}}"
        if selector == _TMPL_ROOT:
            return (
                rf"\sqrt{{{slot(0)}}}"
                if not slot(1)
                else rf"\sqrt[{slot(1)}]{{{slot(0)}}}"
            )
        if selector in (_TMPL_PAREN, _TMPL_BRACK, _TMPL_BAR):
            middle = slot(0)
            delimiters = {
                _TMPL_PAREN: ("(", ")"),
                _TMPL_BRACK: ("[", "]"),
                _TMPL_BAR: ("|", "|"),
            }
            left, right = delimiters[selector]
            left_tex = rf"\left{left}" if node["variation"] & 0x01 else ""
            right_tex = rf"\right{right}" if node["variation"] & 0x02 else ""
            return f"{left_tex}{{{middle}}}{right_tex}"
        if selector == _TMPL_BRACE:
            middle, left, right = slot(0), slot(1), slot(2)
            return (
                "\\left" + (left or r"\{") + "{" + middle + "}\\right" + (right or ".")
            )
        if selector == _TMPL_SUB:
            return (rf"_{{{slot(0)}}}" if slot(0) else "") + (
                rf"^{{{slot(1)}}}" if slot(1) else ""
            )
        if selector == _TMPL_SUP:
            return (rf"^{{{slot(1)}}}" if slot(1) else "") + (
                rf"{{{slot(0)}}}" if slot(0) else ""
            )
        if selector == _TMPL_SUBSUP:
            return (rf"_{{{slot(0)}}}" if slot(0) else "") + (
                rf"^{{{slot(1)}}}" if slot(1) else ""
            )
        if selector == _TMPL_VECTOR:
            contents = slot(0)
            variation = node["variation"]
            if variation & 0x08:
                arrows = {
                    0x01: r"\leftharpoonup",
                    0x02: r"\rightharpoonup",
                    0x03: r"\leftrightharpoons",
                }
            else:
                arrows = {
                    0x01: r"\leftarrow",
                    0x02: r"\rightarrow",
                    0x03: r"\leftrightarrow",
                }
            arrow = arrows.get(variation & 0x03, r"\rightarrow")
            if variation & 0x04:
                return rf"\underset{{{arrow}}}{{{contents}}}"
            if variation == 0x02:
                return rf"\vec{{{contents}}}"
            return rf"\overset{{{arrow}}}{{{contents}}}"
        if selector == _TMPL_HAT:
            return f"{slot(1).rstrip()}{{{slot(0)}}}"
        raise MTEFParseError(f"unsupported MTEF template selector {selector}")
    if kind == "pile":
        return r"\\".join(_render_mtef(child) for child in node["children"])
    if kind == "matrix":
        columns = node["columns"]
        if not columns:
            raise MTEFParseError("MTEF matrix has no columns")
        entries = [_render_mtef(child) for child in node["children"][2:]]
        rows = [
            " & ".join(entries[index : index + columns])
            for index in range(0, len(entries), columns)
        ]
        return (
            r"\begin{array}{" + "c" * columns + "}" + r"\\".join(rows) + r"\end{array}"
        )
    raise MTEFParseError(f"cannot render MTEF node {kind}")


def clean_tex(tex):
    tex = tex.strip()
    while tex.startswith("{") and tex.endswith("}"):
        depth = 0
        is_outer = True
        for i, ch in enumerate(tex):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and i < len(tex) - 1:
                    is_outer = False
                    break
        if is_outer:
            tex = tex[1:-1].strip()
        else:
            break
    return tex


def _equation_native_stream(ole_bytes):
    """Return an OLE Equation Native stream, or ``None`` for non-equations."""
    olefile = _get_olefile()
    if olefile is None:
        return None
    try:
        if not olefile.isOleFile(io.BytesIO(ole_bytes)):
            return None
        with olefile.OleFileIO(io.BytesIO(ole_bytes)) as ole:
            if not ole.exists("Equation Native"):
                return None
            return ole.openstream("Equation Native").read()
    except (OSError, ValueError):
        return None


def extract_latex_from_bytes(ole_bytes):
    """Convert one MathType OLE Equation Native stream into delimiter-free TeX.

    Preview WMF/EMF files are deliberately not accepted here: they contain
    arbitrary binary data and are not an equation source of truth.
    """
    native = _equation_native_stream(ole_bytes)
    if native is None:
        return None

    # MathType can retain manually entered TeX as NUL-terminated metadata.
    tex_marker = b"TeX Input Language"
    tex_index = native.find(tex_marker)
    if tex_index != -1:
        value_start = tex_index + len(tex_marker)
        while value_start < len(native) and native[value_start] in b"\x00\r\n ":
            value_start += 1
        value_end = native.find(b"\x00", value_start)
        if value_end != -1:
            tex = native[value_start:value_end].decode("utf-8", errors="ignore").strip()
            if tex:
                return clean_tex(tex)

    # Equation Native starts with a 28-byte OLE equation header.  Its size
    # field bounds the MTEF payload, preventing a decoder from reading OLE
    # padding or unrelated bytes as equation records.
    if len(native) < 28:
        return None
    header_size = int.from_bytes(native[:2], "little")
    payload_size = int.from_bytes(native[8:12], "little")
    if (
        header_size != 28
        or payload_size == 0
        or header_size + payload_size > len(native)
    ):
        return None

    try:
        tree = _build_mtef_tree(
            _read_mtef_nodes(native[header_size : header_size + payload_size])
        )
        tex = clean_tex(_render_mtef(tree))
        return tex or None
    except MTEFParseError:
        # Preserve the DOCX preview rather than replacing it with malformed TeX.
        return None


def extract_latex_from_ole(ole_bytes):
    return extract_latex_from_bytes(ole_bytes)
