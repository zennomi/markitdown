import sys
from typing import Any, BinaryIO

from .._base_converter import DocumentConverterResult
from .._exceptions import MISSING_DEPENDENCY_MESSAGE, MissingDependencyException
from .._stream_info import StreamInfo
from ..converter_utils.docx.pre_process import post_process_markdown, pre_process_docx
from ._html_converter import HtmlConverter

# Try loading optional (but in this case, required) dependencies
# Save reporting of any exceptions for later
_dependency_exc_info = None
try:
    import mammoth

except ImportError:
    # Preserve the error and stack trace for later
    _dependency_exc_info = sys.exc_info()


ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]

ACCEPTED_FILE_EXTENSIONS = [".docx"]


class DocxConverter(HtmlConverter):
    """
    Converts DOCX files to Markdown. Style information (e.g., headings) and tables are preserved where possible.
    """

    def __init__(self):
        super().__init__()
        self._html_converter = HtmlConverter()

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        # Check: the dependencies
        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__,
                    extension=".docx",
                    feature="docx",
                )
            ) from _dependency_exc_info[1].with_traceback(  # type: ignore[union-attr]
                _dependency_exc_info[2]
            )

        style_map = kwargs.get("style_map", None)
        highlight_style_rule = "highlight => mark"
        if style_map is None:
            style_map = highlight_style_rule
        elif highlight_style_rule not in style_map:
            style_map = f"{style_map}\n{highlight_style_rule}"

        pre_process_stream = pre_process_docx(file_stream)
        html_kwargs = dict(kwargs)
        html_kwargs.setdefault("latex_sup_sub", True)
        html_kwargs.setdefault("docx_highlight", True)
        result = self._html_converter.convert_string(
            mammoth.convert_to_html(pre_process_stream, style_map=style_map).value,
            **html_kwargs,
        )
        result.markdown = post_process_markdown(
            result.markdown, pre_process_stream.generated_tex
        )
        return result
