from __future__ import annotations

import html
import re

from app.infra.settings import ParseMode

_MARKDOWN_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")


def escape_text(text: str, parse_mode: ParseMode) -> str:
    if parse_mode == ParseMode.HTML:
        return html.escape(text)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def format_response(text: str, parse_mode: ParseMode) -> str:
    return escape_text(text, parse_mode)
