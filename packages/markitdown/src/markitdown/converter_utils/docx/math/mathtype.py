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

# MTEF v5 template selectors. See the MathType MTEF v5 specification.
_TMPL_ANGLE = 0
_TMPL_PAREN = 1
_TMPL_BRACE = 2
_TMPL_BRACK = 3
_TMPL_BAR = 4
_TMPL_DBAR = 5
_TMPL_FLOOR = 6
_TMPL_CEILING = 7
_TMPL_OBRACK = 8
_TMPL_INTERVAL = 9
_TMPL_ROOT = 10
_TMPL_FRACTION = 11
_TMPL_UNDERBAR = 12
_TMPL_OVERBAR = 13
_TMPL_ARROW = 14
_TMPL_INTEGRAL = 15
_TMPL_SUM = 16
_TMPL_PRODUCT = 17
_TMPL_COPRODUCT = 18
_TMPL_UNION = 19
_TMPL_INTERSECTION = 20
_TMPL_INTOP = 21
_TMPL_SUMOP = 22
_TMPL_LIMIT = 23
_TMPL_HBRACE = 24
_TMPL_HBRACK = 25
_TMPL_LONG_DIVISION = 26
_TMPL_SUB = 27
_TMPL_SUP = 28
_TMPL_SUBSUP = 29
_TMPL_DIRAC = 30
_TMPL_VECTOR = 31
_TMPL_TILDE = 32
_TMPL_HAT = 33
_TMPL_ARC = 34
_TMPL_JOINT_STATUS = 35
_TMPL_STRIKE = 36
_TMPL_BOX = 37

# MathType stores Unicode MTCode values, not Symbol-font byte values.  Mapping
# these explicitly keeps the generated source portable across TeX renderers.
_TEX_CHARS = {
    0x00A0: "~",
    0x00B0: r"^\circ ",
    0x00B1: r"\pm ",
    0x0302: r"\widehat ",
    0x03B1: r"\alpha ",
    0x03B2: r"\beta ",
    0x03B3: r"\gamma ",
    0x03B4: r"\delta ",
    0x03B5: r"\epsilon ",
    0x03B8: r"\theta ",
    0x03BB: r"\lambda ",
    0x03BC: r"\mu ",
    0x03C3: r"\sigma ",
    0x03C6: r"\phi ",
    0x03C9: r"\omega ",
    0x0394: r"\Delta ",
    0x03C0: r"\pi ",
    0x2115: r"\mathbb{N}",
    0x2190: r"\leftarrow ",
    0x2191: r"\uparrow ",
    0x2192: r"\rightarrow ",
    0x2193: r"\downarrow ",
    0x2194: r"\leftrightarrow ",
    0x21D2: r"\Rightarrow ",
    0x21D4: r"\Leftrightarrow ",
    0x2208: r"\in ",
    0x220F: r"\prod ",
    0x2211: r"\sum ",
    0x2212: "-",
    0x2210: r"\coprod ",
    0x221A: r"\sqrt{}",
    0x222B: r"\int ",
    0x222C: r"\iint ",
    0x222D: r"\iiint ",
    0x222E: r"\oint ",
    0x2229: r"\cap ",
    0x22A5: r"\bot ",
    0x22C5: r"\cdot ",
    0x22EE: r"\vdots ",
    0x223C: r"\sim ",
    0x2264: r"\leq ",
    0x2265: r"\geq ",
    0x22C2: r"\bigcap ",
    0x22C3: r"\bigcup ",
    0xEF02: r"\,",
    0xEF05: r"\quad ",
}


class MTEFParseError(ValueError):
    """The Equation Native stream is not a complete MTEF equation."""


class MTEFReader:
    """Strict, little-endian reader for an MTEF v5 record stream."""

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

    def read_unsigned(self):
        """Read MTEF's one- or three-byte unsigned integer encoding."""
        value = self.read_u8()
        return self.read_u16() if value == 0xFF else value

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
    # A compact nudge has two biased bytes. Two 0x80 bytes mark an extended
    # nudge, whose signed x/y coordinates each occupy two additional bytes.
    x = reader.read_u8()
    y = reader.read_u8()
    if x == 0x80 and y == 0x80:
        reader.skip(4)


def _skip_ruler(reader):
    if reader.read_u8() != 7:
        raise MTEFParseError("expected ruler record")
    reader.skip(reader.read_u8() * 3)


def _skip_size(reader):
    size = reader.read_u8()
    if size == 100:
        reader.skip(3)
    elif size == 101:
        reader.skip(2)
    else:
        reader.skip(1)


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
        if reader.read_unsigned() != 0:
            reader.read_u8()  # character style


def _skip_legacy_ruler(reader):
    if reader.read_u8() & 0x0F != 7:
        raise MTEFParseError("expected ruler record")
    reader.skip(reader.read_u8() * 3)


def _read_legacy_mtef_nodes(data, version):
    """Read the documented MTEF v3/v4 tag-byte record layout."""
    reader = MTEFReader(data)
    if reader.read_u8() != version:
        raise MTEFParseError("MTEF version changed while parsing")
    reader.skip(4)  # platform, product, version, and version subversion

    nodes = []
    while reader.pos < len(reader.data):
        tag = reader.read_u8()
        record, options = tag & 0x0F, tag >> 4
        if record == _MTEF_END:
            nodes.append(_node("end"))
        elif record == _MTEF_LINE:
            if options & 0x08:
                _skip_nudge(reader)
            if options & 0x04:
                reader.skip(2)
            if options & 0x02:
                _skip_legacy_ruler(reader)
            nodes.append(_node("line", null=bool(options & 0x01)))
        elif record == _MTEF_CHAR:
            if options & 0x08:
                _skip_nudge(reader)
            nodes.append(
                _node("char", typeface=reader.read_u8(), mtcode=reader.read_u16())
            )
        elif record == _MTEF_TMPL:
            if options & 0x08:
                _skip_nudge(reader)
            nodes.append(
                _node(
                    "template",
                    selector=reader.read_u8(),
                    variation=reader.read_u8(),
                    version=version,
                )
            )
            reader.read_u8()  # template options
        elif record == _MTEF_PILE:
            if options & 0x08:
                _skip_nudge(reader)
            reader.skip(2)  # horizontal and vertical alignment
            if options & 0x02:
                _skip_legacy_ruler(reader)
            nodes.append(_node("pile"))
        elif record == _MTEF_MATRIX:
            if options & 0x08:
                _skip_nudge(reader)
            reader.skip(3)  # vertical, horizontal, and vertical justification
            rows, columns = reader.read_u8(), reader.read_u8()
            reader.skip((rows + 4) // 4)
            reader.skip((columns + 4) // 4)
            nodes.append(_node("matrix", rows=rows, columns=columns))
        elif record == _MTEF_EMBELL:
            if options & 0x08:
                _skip_nudge(reader)
            nodes.append(_node("embellishment", embellishment=reader.read_u8()))
        elif record == 7:
            reader.skip(reader.read_u8() * 3)
        elif record == _MTEF_FONT_STYLE_DEF:
            reader.skip(2)  # typeface and style
            reader.read_c_string()
        elif record == _MTEF_SIZE:
            _skip_size(reader)
        elif record in (_MTEF_FULL, _MTEF_SUB, _MTEF_SUB2, _MTEF_SYM, _MTEF_SUBSYM):
            pass
        else:
            raise MTEFParseError(f"unsupported MTEF v{version} record {record}")
    return nodes


def _read_mtef_nodes(data):
    """Read a documented MTEF v5 record stream into a flat node sequence."""
    reader = MTEFReader(data)
    version = reader.read_u8()
    if version in (3, 4):
        return _read_legacy_mtef_nodes(data, version)
    if version != 5:
        raise MTEFParseError(f"unsupported MTEF version {version}")

    # platform, product, version, version sub, application key, inline flag
    reader.skip(4)
    reader.read_c_string()
    reader.read_u8()

    nodes = []
    while reader.pos < len(reader.data):
        record = reader.read_u8()
        if record >= _MTEF_FUTURE:
            reader.skip(reader.read_unsigned())
        elif record == _MTEF_END:
            nodes.append(_node("end"))
        elif record == _MTEF_LINE:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            if options & 0x04:
                reader.skip(2)
            if options & 0x02:
                _skip_ruler(reader)
            nodes.append(_node("line", null=bool(options & 0x01)))
        elif record == _MTEF_CHAR:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            typeface = reader.read_u8()
            mtcode = None if options & 0x20 else reader.read_u16()
            font_position = None
            if options & 0x04:
                font_position = reader.read_u8()
            if options & 0x10:
                font_position = reader.read_u16()
            if mtcode is None:
                # Some MathType OLE objects retain only a font position. It
                # is often an ASCII-compatible MTCode, and retaining it is
                # preferable to discarding the complete equation preview.
                if font_position is None:
                    raise MTEFParseError("character record has no character code")
                mtcode = font_position
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
            if options & 0x02:
                _skip_ruler(reader)
            nodes.append(_node("pile"))
        elif record == _MTEF_MATRIX:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            reader.skip(3)  # vertical, horizontal, and vertical justification
            rows = reader.read_u8()
            columns = reader.read_u8()
            # Each partition line has a two-bit style; there is one more line
            # than there are rows or columns, respectively.
            reader.skip((rows + 4) // 4)
            reader.skip((columns + 4) // 4)
            nodes.append(_node("matrix", rows=rows, columns=columns))
        elif record == _MTEF_EMBELL:
            options = reader.read_u8()
            if options & 0x08:
                _skip_nudge(reader)
            nodes.append(_node("embellishment", embellishment=reader.read_u8()))
        elif record == _MTEF_FONT_STYLE_DEF:
            reader.read_unsigned()  # font definition index
            reader.read_u8()  # character style
        elif record == _MTEF_FONT_DEF:
            reader.read_unsigned()  # encoding definition index
            reader.read_c_string()
        elif record == _MTEF_EQN_PREFS:
            _skip_equation_preferences(reader)
        elif record == _MTEF_ENCODING_DEF:
            reader.read_c_string()
        elif record == _MTEF_SIZE:
            _skip_size(reader)
        elif record == _MTEF_COLOR:
            reader.read_unsigned()
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
            # Accent commands precede their character in TeX but follow it in
            # MTEF. Primes are suffixes and intentionally retain MTEF order.
            if current["embellishment"] not in (5, 6, 7, 18) and len(children) >= 2:
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


def _fence(middle, variation, left, right):
    left_tex = rf"\left{left}" if variation & 0x01 else ""
    right_tex = rf"\right{right}" if variation & 0x02 else ""
    return f"{left_tex}{{{middle}}}{right_tex}"


def _scripts(lower, upper):
    return (rf"_{{{lower}}}" if lower else "") + (rf"^{{{upper}}}" if upper else "")


def _big_operator(operator, main, upper, lower):
    scripts = _scripts(lower, upper)
    # TeX consumes adjacent letters as part of a control word. A bare operator
    # (for example, ``\\bigcup``) therefore needs a delimiter before its body.
    separator = " " if operator.startswith("\\") and not scripts else ""
    return f"{operator}{separator}{scripts}{main}"


def _render_legacy_template(selector, variation, slot):
    """Render MTEF v3/v4 templates, whose selector table differs from v5."""
    fences = {
        0: (r"\langle", r"\rangle"),
        1: ("(", ")"),
        2: (r"\{", r"\}"),
        3: ("[", "]"),
        4: ("|", "|"),
        5: (r"\|", r"\|"),
        6: (r"\lfloor", r"\rfloor"),
        7: (r"\lceil", r"\rceil"),
        8: (r"\{", r"\{"),
        9: (r"\}", r"\}"),
        10: (r"\}", r"\{"),
        11: (r"\{", ")"),
        12: ("(", r"\}"),
    }
    if selector in fences:
        flags = {0: 0x03, 1: 0x01, 2: 0x02}.get(variation, 0x03)
        return _fence(slot(0), flags, *fences[selector])
    if selector == 13:
        return (
            rf"\sqrt{{{slot(0)}}}" if not slot(1) else rf"\sqrt[{slot(1)}]{{{slot(0)}}}"
        )
    if selector == 14:
        return rf"\frac{{{slot(0)}}}{{{slot(1)}}}"
    if selector == 15:
        if variation == 0:
            return _scripts("", slot(1))
        if variation == 1:
            return _scripts(slot(0), "")
        return _scripts(slot(0), slot(1))
    if selector in (16, 17):
        macro = r"\underline" if selector == 16 else r"\overline"
        result = rf"{macro}{{{slot(0)}}}"
        return rf"{macro}{{{result}}}" if variation & 0x01 else result
    if selector in (18, 19, 20):
        arrow = {18: r"\leftarrow", 19: r"\rightarrow", 20: r"\leftrightarrow"}[
            selector
        ]
        macro = r"\underset" if variation == 0 else r"\overset"
        return rf"{macro}{{{slot(1).rstrip() or arrow}}}{{{slot(0)}}}"
    if selector in range(21, 27):
        operators = {
            21: r"\int",
            22: r"\iint",
            23: r"\iiint",
            24: r"\int",
            25: r"\iint",
            26: r"\iiint",
        }
        operator = operators[selector]
        if selector == 21 and variation in (3, 4):
            operator = r"\oint"
        return _big_operator(operator, slot(0), slot(1), slot(2))
    if selector in (27, 28):
        macro, suffix = (
            (r"\overbrace", "^") if selector == 27 else (r"\underbrace", "_")
        )
        return rf"{macro}{{{slot(0)}}}{suffix}{{{slot(1)}}}"
    if selector in (29, 30, 31, 32, 33, 34, 35, 36, 37, 38):
        operators = {
            29: r"\sum",
            30: r"\sum",
            31: r"\prod",
            32: r"\prod",
            33: r"\coprod",
            34: r"\coprod",
            35: r"\bigcup",
            36: r"\bigcup",
            37: r"\bigcap",
            38: r"\bigcap",
        }
        return _big_operator(operators[selector], slot(0), slot(1), slot(2))
    if selector == 39:
        return _big_operator(r"\lim", slot(0), slot(2), slot(1))
    if selector == 40:
        return rf"\overline{{{slot(0)}}}){slot(1)}"
    if selector == 41:
        return rf"{{{slot(0)}}}/{{{slot(1)}}}"
    if selector in (42, 43):
        return _big_operator(
            slot(3).rstrip() or r"\operatorname{op}", slot(0), slot(1), slot(2)
        )
    if selector == 44:
        return _scripts(slot(0), slot(1))
    if selector == 45:
        left = r"\langle" if variation in (0, 1) else ""
        right = r"\rangle" if variation in (0, 2) else ""
        return f"{left}{slot(0)}|{slot(1)}{right}"
    if selector in (46, 47):
        arrow = {0: r"\leftarrow", 1: r"\rightarrow", 2: r"\leftrightarrow"}.get(
            variation, r"\rightarrow"
        )
        macro = r"\underset" if selector == 46 else r"\overset"
        return rf"{macro}{{{arrow}}}{{{slot(0)}}}"
    if selector == 48:
        return rf"\overset{{\frown}}{{{slot(0)}}}"
    return "".join(slot(index) for index in range(4))


def _render_mtef(node):
    kind = node["kind"]
    if kind == "root" or kind == "line":
        return "".join(_render_mtef(child) for child in node["children"])
    if kind == "char":
        return _tex_character(node["mtcode"], node["typeface"])
    if kind == "embellishment":
        marks = {
            2: r"\dot ",
            3: r"\ddot ",
            4: r"\dddot ",
            5: "'",
            6: "''",
            7: r"{}'",
            8: r"\tilde ",
            9: r"\hat ",
            10: r"\not ",
            11: r"\overrightarrow ",
            12: r"\overleftarrow ",
            13: r"\overleftrightarrow ",
            14: r"\rightharpoonup ",
            15: r"\leftharpoonup ",
            16: r"\mid ",
            17: r"\bar ",
            18: "'''",
            19: r"\frown ",
            20: r"\smile ",
            21: r"\parallel ",
            22: r"\diagup ",
            23: r"\diagdown ",
            24: r"\ddddot ",
            25: r"\underdot ",
            26: r"\underddot ",
            27: r"\underdddot ",
            28: r"\underddddot ",
            29: r"\underline ",
            30: r"\utilde ",
            31: r"\underfrown ",
            32: r"\undersmile ",
            33: r"\underrightarrow ",
            34: r"\underleftarrow ",
            35: r"\underleftrightarrow ",
            36: r"\underrightharpoonup ",
            37: r"\underleftharpoonup ",
        }
        return marks.get(node["embellishment"], "")
    if kind == "template":
        slots = [_render_mtef(child) for child in node["children"]]
        slot = lambda index: slots[index] if index < len(slots) else ""
        selector = node["selector"]
        variation = node["variation"]
        if node.get("version", 5) < 5:
            return _render_legacy_template(selector, variation, slot)

        fences = {
            _TMPL_ANGLE: (r"\langle", r"\rangle"),
            _TMPL_PAREN: ("(", ")"),
            _TMPL_BRACE: (r"\{", r"\}"),
            _TMPL_BRACK: ("[", "]"),
            _TMPL_BAR: ("|", "|"),
            _TMPL_DBAR: (r"\|", r"\|"),
            _TMPL_FLOOR: (r"\lfloor", r"\rfloor"),
            _TMPL_CEILING: (r"\lceil", r"\rceil"),
            _TMPL_OBRACK: ("[", "]"),
        }
        if selector in fences:
            return _fence(slot(0), variation, *fences[selector])
        if selector == _TMPL_INTERVAL:
            left = ("(", ")", "[", "]")[variation & 0x03]
            right = ("(", ")", "[", "]")[(variation >> 4) & 0x03]
            return rf"\left{left}{{{slot(0)}}}\right{right}"
        if selector == _TMPL_FRACTION:
            if variation & 0x02:
                return rf"{{{slot(0)}}}/{{{slot(1)}}}"
            return rf"\frac{{{slot(0)}}}{{{slot(1)}}}"
        if selector == _TMPL_ROOT:
            return (
                rf"\sqrt{{{slot(0)}}}"
                if not slot(1)
                else rf"\sqrt[{slot(1)}]{{{slot(0)}}}"
            )
        if selector in (_TMPL_UNDERBAR, _TMPL_OVERBAR):
            macro = r"\underline" if selector == _TMPL_UNDERBAR else r"\overline"
            result = rf"{macro}{{{slot(0)}}}"
            return rf"{macro}{{{result}}}" if variation & 0x01 else result
        if selector == _TMPL_ARROW:
            direction = variation & 0x30
            arrow = {0x10: r"\leftarrow", 0x20: r"\rightarrow"}.get(
                direction, r"\leftrightarrow" if direction == 0x30 else r"\rightarrow"
            )
            if variation & 0x02:
                arrow = {
                    0x10: r"\leftharpoonup",
                    0x20: r"\rightharpoonup",
                    0x30: r"\leftrightharpoons",
                }.get(direction, r"\rightharpoonup")
            arrow = slot(1).rstrip() or arrow
            macro = r"\underset" if variation & 0x08 else r"\overset"
            return rf"{macro}{{{arrow}}}{{{slot(0)}}}"
        if selector in (
            _TMPL_INTEGRAL,
            _TMPL_SUM,
            _TMPL_PRODUCT,
            _TMPL_COPRODUCT,
            _TMPL_UNION,
            _TMPL_INTERSECTION,
            _TMPL_INTOP,
            _TMPL_SUMOP,
        ):
            operators = {
                _TMPL_INTEGRAL: {1: r"\int", 2: r"\iint", 3: r"\iiint", 4: r"\oint"},
                _TMPL_SUM: {0: r"\sum"},
                _TMPL_PRODUCT: {0: r"\prod"},
                _TMPL_COPRODUCT: {0: r"\coprod"},
                _TMPL_UNION: {0: r"\bigcup"},
                _TMPL_INTERSECTION: {0: r"\bigcap"},
                _TMPL_INTOP: {0: r"\operatorname{op}"},
                _TMPL_SUMOP: {0: r"\operatorname{op}"},
            }
            operator_map = operators[selector]
            operator = (
                slot(3).rstrip()
                or operator_map.get(variation & 0x0C)
                or operator_map.get(variation & 0x03)
                or operator_map.get(0, r"\int")
            )
            return _big_operator(operator, slot(0), slot(1), slot(2))
        if selector == _TMPL_LIMIT:
            return _big_operator(r"\lim", slot(0), slot(2), slot(1))
        if selector in (_TMPL_HBRACE, _TMPL_HBRACK):
            macro = {
                _TMPL_HBRACE: (r"\underbrace", r"\overbrace"),
                _TMPL_HBRACK: (r"\underbracket", r"\overbracket"),
            }[selector][bool(variation & 0x01)]
            suffix = "^" if variation & 0x01 else "_"
            return rf"{macro}{{{slot(0)}}}{suffix}{{{slot(1)}}}"
        if selector == _TMPL_LONG_DIVISION:
            return rf"\overline{{{slot(0)}}}){slot(1)}"
        if selector in (_TMPL_SUB, _TMPL_SUP, _TMPL_SUBSUP):
            return _scripts(slot(0), slot(1))
        if selector == _TMPL_DIRAC:
            left = r"\langle" if variation & 0x01 else ""
            right = r"\rangle" if variation & 0x02 else ""
            return f"{left}{slot(0)}|{slot(1)}{right}"
        if selector == _TMPL_VECTOR:
            arrows = {
                0x01: r"\leftarrow",
                0x02: r"\rightarrow",
                0x03: r"\leftrightarrow",
            }
            arrow = arrows.get(variation & 0x03, r"\rightarrow")
            if variation & 0x08:
                arrow = {
                    0x01: r"\leftharpoonup",
                    0x02: r"\rightharpoonup",
                    0x03: r"\leftrightharpoons",
                }.get(variation & 0x03, r"\rightharpoonup")
            if variation & 0x04:
                return rf"\underset{{{arrow}}}{{{slot(0)}}}"
            if variation == 0x02:
                return rf"\vec{{{slot(0)}}}"
            return rf"\overset{{{arrow}}}{{{slot(0)}}}"
        if selector in (_TMPL_TILDE, _TMPL_HAT, _TMPL_ARC, _TMPL_JOINT_STATUS):
            macros = {
                _TMPL_TILDE: r"\widetilde",
                _TMPL_HAT: r"\widehat",
                _TMPL_ARC: r"\overset{\frown}",
                _TMPL_JOINT_STATUS: r"\overset{\smile}",
            }
            return rf"{macros[selector]}{{{slot(0)}}}"
        if selector == _TMPL_STRIKE:
            if variation & 0x01:
                return rf"\cancel{{{slot(0)}}}"
            return rf"\not{{{slot(0)}}}"
        if selector == _TMPL_BOX:
            return rf"\boxed{{{slot(0)}}}"
        # MTEF reserves selectors for future extensions. Preserve their slots
        # as valid TeX instead of discarding an entire otherwise-readable OLE.
        return "".join(slots)
    if kind == "pile":
        return r"\\".join(_render_mtef(child) for child in node["children"])
    if kind == "matrix":
        columns = node["columns"]
        if not columns:
            raise MTEFParseError("MTEF matrix has no columns")
        entries = [_render_mtef(child) for child in node["children"]]
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
        # An empty but structurally valid stream is an intentional blank
        # equation, distinct from a malformed stream (which returns None).
        return clean_tex(_render_mtef(tree))
    except MTEFParseError:
        # Preserve the DOCX preview rather than replacing it with malformed TeX.
        return None


def extract_latex_from_ole(ole_bytes):
    return extract_latex_from_bytes(ole_bytes)
