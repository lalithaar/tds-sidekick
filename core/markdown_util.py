import bleach
from markdown import markdown

ALLOWED_TAGS = [
    'a', 'abbr', 'b', 'blockquote', 'br', 'caption', 'code', 'col', 'colgroup',
    'del', 'div', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img',
    'li', 'ol', 'p', 'pre', 'span', 'strike', 'strong', 'sub', 'sup', 'table',
    'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
]

ALLOWED_ATTRS = {
    'a': ['href', 'title'],
    'img': ['src', 'alt', 'title'],
    'code': ['class'],
    'div': ['class'],
    'pre': ['class'],
    'span': ['class'],
    'th': ['align', 'colspan', 'rowspan'],
    'td': ['align', 'colspan', 'rowspan'],
}

ALLOWED_PROTOCOLS = {'http', 'https', 'mailto'}


def render_markdown(text):
    if not text:
        return ''
    html = markdown(
        str(text),
        extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists', 'codehilite'],
        extension_configs={'codehilite': {'guess_lang': False}},
    )
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )