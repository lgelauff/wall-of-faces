"""
weasyprint_worker.py — WeasyPrint subprocess entry point.

Called by render.py as a subprocess:
    python3 src/weasyprint_worker.py <html_path> <pdf_path>

Running WeasyPrint in a subprocess (rather than in-process) is required because
`python3 -m weasyprint` and direct `HTML(...).write_pdf()` calls bypass any
custom url_fetcher set by the caller.  Instantiating WeasyPrint here, in a
dedicated subprocess, guarantees safe_url_fetcher is always in effect.

safe_url_fetcher allows only file:// URLs.  All image src= values in the card
HTML are either:
  - file:///absolute/path  (snapshot images — allowed)
  - data:image/svg+xml,... (inline SVG — handled natively by WeasyPrint,
                             never passed to the fetcher)
  - absent / None          (placeholders — no fetch needed)

Any external https:// URL reaching the fetcher is a bug; it raises ValueError
and aborts the render with a non-zero exit code.

Exit codes:
    0 — success; PDF written to <pdf_path>
    1 — error; message printed to stderr
"""

import sys
from typing import Any


def safe_url_fetcher(url: str, **kwargs: Any) -> Any:
    """Block all non-file URLs.  Inline data: URIs are handled by WeasyPrint
    before they reach this function, so they do not need to be allowed here."""
    if url.startswith('file:///'):
        from weasyprint import default_url_fetcher
        return default_url_fetcher(url, **kwargs)
    raise ValueError(f'WeasyPrint blocked non-file URL: {url!r}')


def main() -> None:
    if len(sys.argv) != 3:
        print(
            f'Usage: python3 {sys.argv[0]} <html_path> <pdf_path>',
            file=sys.stderr,
        )
        sys.exit(1)

    html_path = sys.argv[1]
    pdf_path  = sys.argv[2]

    try:
        from weasyprint import HTML
        HTML(filename=html_path, url_fetcher=safe_url_fetcher).write_pdf(pdf_path)
    except Exception as exc:
        print(f'WeasyPrint error: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
