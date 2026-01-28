import re
import markdownify

from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse, urlunparse


class _CustomMarkdownify(markdownify.MarkdownConverter):
    """
    A custom version of markdownify's MarkdownConverter. Changes include:

    - Altering the default heading style to use '#', '##', etc.
    - Removing javascript hyperlinks.
    - Truncating images with large data:uri sources.
    - Ensuring URIs are properly escaped, and do not conflict with Markdown syntax
    """

    def __init__(self, **options: Any):
        options["heading_style"] = options.get("heading_style", markdownify.ATX)
        options["keep_data_uris"] = options.get("keep_data_uris", False)
        options["latex_sup_sub"] = options.get("latex_sup_sub", False)
        # Explicitly cast options to the expected type if necessary
        super().__init__(**options)

    def convert_hn(
        self,
        n: int,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ) -> str:
        """Same as usual, but be sure to start with a new line"""
        if not convert_as_inline:
            if not re.search(r"^\n", text):
                return "\n" + super().convert_hn(n, el, text, convert_as_inline)  # type: ignore

        return super().convert_hn(n, el, text, convert_as_inline)  # type: ignore

    def convert_a(
        self,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ):
        """Same as usual converter, but removes Javascript links and escapes URIs."""
        prefix, suffix, text = markdownify.chomp(text)  # type: ignore
        if not text:
            return ""

        if el.find_parent("pre") is not None:
            return text

        href = el.get("href")
        title = el.get("title")

        # Escape URIs and skip non-http or file schemes
        if href:
            try:
                parsed_url = urlparse(href)  # type: ignore
                if parsed_url.scheme and parsed_url.scheme.lower() not in ["http", "https", "file"]:  # type: ignore
                    return "%s%s%s" % (prefix, text, suffix)
                href = urlunparse(parsed_url._replace(path=quote(unquote(parsed_url.path))))  # type: ignore
            except ValueError:  # It's not clear if this ever gets thrown
                return "%s%s%s" % (prefix, text, suffix)

        # For the replacement see #29: text nodes underscores are escaped
        if (
            self.options["autolinks"]
            and text.replace(r"\_", "_") == href
            and not title
            and not self.options["default_title"]
        ):
            # Shortcut syntax
            return "<%s>" % href
        if self.options["default_title"] and not title:
            title = href
        title_part = ' "%s"' % title.replace('"', r"\"") if title else ""
        return (
            "%s[%s](%s%s)%s" % (prefix, text, href, title_part, suffix)
            if href
            else text
        )

    def convert_img(
        self,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ) -> str:
        """Same as usual converter, but removes data URIs"""

        alt = el.attrs.get("alt", None) or ""
        src = el.attrs.get("src", None) or el.attrs.get("data-src", None) or ""
        title = el.attrs.get("title", None) or ""
        title_part = ' "%s"' % title.replace('"', r"\"") if title else ""
        # Remove all line breaks from alt
        alt = alt.replace("\n", " ")
        if (
            convert_as_inline
            and el.parent.name not in self.options["keep_inline_images_in"]
        ):
            return alt

        # Remove dataURIs
        if src.startswith("data:") and not self.options["keep_data_uris"]:
            src = src.split(",")[0] + "..."

        return "![%s](%s%s)" % (alt, src, title_part)

    def convert_input(
        self,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ) -> str:
        """Convert checkboxes to Markdown [x]/[ ] syntax."""

        if el.get("type") == "checkbox":
            return "[x] " if el.has_attr("checked") else "[ ] "
        return ""

    _SUP_START = "__MARKITDOWN_SUP_START__"
    _SUP_END = "__MARKITDOWN_SUP_END__"
    _SUB_START = "__MARKITDOWN_SUB_START__"
    _SUB_END = "__MARKITDOWN_SUB_END__"

    def convert_sup(
        self,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ) -> str:
        if not self.options.get("latex_sup_sub", False):
            return super().convert_sup(el, text, convert_as_inline, **kwargs)  # type: ignore
        return f"{self._SUP_START}{text}{self._SUP_END}"

    def convert_sub(
        self,
        el: Any,
        text: str,
        convert_as_inline: Optional[bool] = False,
        **kwargs,
    ) -> str:
        if not self.options.get("latex_sup_sub", False):
            return super().convert_sub(el, text, convert_as_inline, **kwargs)  # type: ignore
        return f"{self._SUB_START}{text}{self._SUB_END}"

    @staticmethod
    def _format_latex_script(script_text: str, script_char: str) -> str:
        cleaned = script_text.strip()
        if re.fullmatch(r"[A-Za-z0-9]+", cleaned):
            return f"{script_char}{cleaned}"
        return f"{script_char}{{{cleaned}}}"

    def _convert_latex_sup_sub(self, text: str) -> str:
        sup_pattern = re.compile(
            rf"(?P<base>[A-Za-z0-9]+){self._SUP_START}(?P<script>.*?){self._SUP_END}"
        )
        sub_pattern = re.compile(
            rf"(?P<base>[A-Za-z0-9]+){self._SUB_START}(?P<script>.*?){self._SUB_END}"
        )

        def sup_repl(match: re.Match[str]) -> str:
            base = match.group("base")
            script = self._format_latex_script(match.group("script"), "^")
            return f"${base}{script}$"

        def sub_repl(match: re.Match[str]) -> str:
            base = match.group("base")
            script = self._format_latex_script(match.group("script"), "_")
            return f"${base}{script}$"

        updated = sup_pattern.sub(sup_repl, text)
        updated = sub_pattern.sub(sub_repl, updated)

        updated = updated.replace(self._SUP_START, "^").replace(self._SUP_END, "")
        updated = updated.replace(self._SUB_START, "_").replace(self._SUB_END, "")
        return updated

    def convert_soup(self, soup: Any) -> str:
        converted = super().convert_soup(soup)  # type: ignore
        if self.options.get("latex_sup_sub", False):
            return self._convert_latex_sup_sub(converted)
        return converted
