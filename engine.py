"""Novel Processor engine: readers, chapter detection, writers and the batch runner.

Everything here is GUI-free. Callers pass a ``log(str)`` callback and a
``progress(float 0..1)`` callback; both may be invoked from worker threads.
"""
from __future__ import annotations

import csv
import html
import os
import posixpath
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote
from xml.sax.saxutils import escape as xml_escape

SUPPORTED_EXT = (".txt", ".pdf", ".epub")
DEFAULT_WORKERS = 2

LogFn = Callable[[str], None]
ProgressFn = Callable[[float], None]
Section = tuple[str, list[str]]  # (title, body lines)

# PDFium is not thread-safe, so PDF reads are serialised.
_PDF_LOCK = threading.Lock()
_FONT_LOCK = threading.Lock()
_pdf_fonts: tuple[str, str] | None = None


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

# Korean platform age-rating boilerplate that gets injected into exported novels.
_AGE_NOTICE = re.compile(
    r"\[?\s*\d{1,2}\s*\uc138\s*(\uc774\uc6a9\uac00|\uc774\uc6a9\ubd88\uac00|\uad00\ub78c\uac00)?\s*\uc548\ub0b4\s*\]?\s*\n?"
    r"|\ubcf8\s*\uc791\ud488\uc740\s*\d{1,2}\s*\uc138\s*\ubbf8\ub9cc\uc758\s*\uccad\uc18c\ub144\uc774\s*\uc5f4\ub78c\ud558\uae30\uc5d0\s*\ubd80\uc801\uc808\ud55c\s*\ub0b4\uc6a9\uc744\s*\ud3ec\ud568\ud558\uace0\s*\uc788\uc2b5\ub2c8\ub2e4\."
    r"\s*\ubcf4\ud638\uc790\uc758\s*\uc9c0\ub3c4\s*\ud558\uc5d0\s*\uc791\ud488\uc744\s*\uac10\uc0c1\ud574\uc8fc\uc2dc\uae30\s*\ubc14\ub78d\ub2c8\ub2e4\.\s*")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f\ufeff\u200b]", "", text)
    return _AGE_NOTICE.sub("", text)


def read_txt(path: Path) -> str:
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    guess = None
    try:
        import chardet
        res = chardet.detect(data[:200_000])
        if res.get("encoding") and (res.get("confidence") or 0) >= 0.8:
            guess = res["encoding"]
    except ImportError:
        pass
    for enc in filter(None, (guess, "cp949", "gb18030")):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def read_pdf(path: Path) -> str:
    import pypdfium2 as pdfium
    with _PDF_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            pages = []
            for page in pdf:
                textpage = page.get_textpage()
                pages.append(textpage.get_text_bounded())
                textpage.close()
                page.close()
            return "\n".join(pages)
        finally:
            pdf.close()


_BLOCK_TAGS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
               "blockquote", "section", "article", "hr", "pre"]


def _html_to_text(data: bytes) -> tuple[str | None, str]:
    import warnings

    from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(data, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    body = soup.body or soup
    for s in list(body.find_all(string=True)):
        if type(s) is NavigableString:
            collapsed = re.sub(r"\s+", " ", s)
            if collapsed != s:
                s.replace_with(collapsed)
    heading = body.find(["h1", "h2", "h3"])
    title = heading.get_text(" ", strip=True) if heading else None
    for br in body.find_all("br"):
        br.replace_with("\n")
    for tag in body.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    lines = [ln.strip() for ln in body.get_text().split("\n")]
    return title or None, "\n".join(ln for ln in lines if ln)


def read_epub_sections(path: Path) -> list[tuple[str | None, str]]:
    """Return (title, text) for every spine document that has text."""
    from lxml import etree
    out = []
    with zipfile.ZipFile(path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//{*}rootfile").get("full-path")
        opf = etree.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest = {it.get("id"): it for it in opf.iter("{*}item")}
        names = set(z.namelist())
        for ref in opf.iter("{*}itemref"):
            item = manifest.get(ref.get("idref"))
            if item is None or "nav" in (item.get("properties") or "").split():
                continue
            full = posixpath.normpath(posixpath.join(base, unquote(item.get("href", ""))))
            if full not in names:
                continue
            title, text = _html_to_text(z.read(full))
            if text.strip():
                out.append((title, text))
    return out


def read_text(path: Path) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        text = read_pdf(path)
    elif ext == ".epub":
        text = "\n".join(t for _, t in read_epub_sections(path))
    else:
        text = read_txt(path)
    return _clean(text)


# --------------------------------------------------------------------------
# Chapter heading rules
# --------------------------------------------------------------------------

_CJK = "零〇一二三四五六七八九十百千万两"
_COUNTERS = "화장회편권부막章话話回节節卷集"
_ROMAN_OK = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_SEP = r"(?:\s*[:.\-–—]\s*|\s+|$)"


def _roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or not _ROMAN_OK.match(s):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for ch, nxt in zip(s, s[1:] + " "):
        v = vals[ch]
        total += -v if nxt in vals and vals[nxt] > v else v
    return total


def _cjk_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    if all(c in digits for c in s):
        return int("".join(str(digits[c]) for c in s))
    total = section = num = 0
    for c in s:
        if c in digits:
            num = digits[c]
        elif c in units:
            section += (num or 1) * units[c]
            num = 0
        elif c == "万":
            total += (section + num) * 10000
            section = num = 0
        else:
            return None
    return total + section + num


class Rule:
    """A heading pattern. Named groups: ``n`` = number, ``t`` = optional title."""

    def __init__(self, name: str, pattern: str, numtype: str | None = "int",
                 sequential: bool = False):
        self.name = name
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.numtype = numtype
        self.sequential = sequential  # keep only headings forming a rising sequence

    def match(self, s: str) -> tuple[int | None, str] | None:
        m = self.regex.match(s)
        if not m:
            return None
        gd = m.groupdict()
        number = None
        if self.numtype:
            raw = gd.get("n") or ""
            number = (int(raw) if self.numtype == "int" and raw.isdigit()
                      else _roman_to_int(raw) if self.numtype == "roman"
                      else _cjk_to_int(raw) if self.numtype == "cjk" else None)
            if number is None:
                return None
        return number, (gd.get("t") or "").strip()


class TitleListRule:
    """Matches lines against the episode list fetched from Naver Series.

    Headings are rewritten to the canonical "<N화> <title>" form, which also
    drops irregular tags such as (삽화) / [수정] / <완결>.
    """

    numtype = "int"
    sequential = False
    _TAGS = re.compile(r"\s*[\(\[<]?\s*(?:삽화|수정|완결|무료|마지막).*?[\)\]>]*\s*$")
    _ANY_TAG = re.compile(r"\s*[\[\(<][^\]\)>]*[\]\)>]\s*\.?\s*$")
    _NUM_PREFIX = re.compile(r"^[#＃]?\s*제?\s*\d+\s*[화권장편]?\.?\s*-?\s*")

    def __init__(self, entries: list[tuple[str, str]]):
        self.name = f"Naver Series episode list ({len(entries)} episodes)"
        self.count = len(entries)
        self.lookup: dict[str, tuple[int, str, str]] = {}
        for order, (chap_num, title) in enumerate(entries, 1):
            digits = re.sub(r"\D", "", chap_num)
            number = int(digits) if digits else order
            clean = self._TAGS.sub("", title).strip() if title else ""
            combined = f"{chap_num} {clean}".strip()
            keys = []
            if title:
                keys += [title, f"{chap_num} {title}"]
                if clean:
                    keys += [clean, combined]
                    no_hanja = re.sub(r"\s*\([^)]*\)\s*", " ", clean).strip()
                    if no_hanja:
                        keys += [no_hanja, f"{chap_num} {no_hanja}"]
            if chap_num:
                keys.append(chap_num)
                if digits:
                    keys += [f"{digits}화", f"제{digits}화", f"#{digits}화", f"＃{digits}화"]
            for key in keys:
                self.lookup.setdefault(self._norm(key), (number, clean, combined))

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    def match(self, s: str):
        s = s.strip(" \t<>")
        no_tag = self._ANY_TAG.sub("", self._TAGS.sub("", s)).rstrip(". ")
        for variant in (s, no_tag, self._NUM_PREFIX.sub("", no_tag, count=1)):
            key = self._norm(variant)
            if len(key) >= 2 or key.isdigit():
                hit = self.lookup.get(key)
                if hit:
                    return hit
        return None


RULE_NUMERIC = Rule("00 Prologue / 1 / 01",
                    r"^(?P<n>\d{1,5})(?:\.?\s+(?P<t>\S.{0,60}))?$", sequential=True)
RULE_KOREAN = Rule("N화 (Korean)",
                   r"^(?:제\s*)?(?P<n>\d{1,6})\s*화\.?(?:\s+(?P<t>.{1,100}))?$")
RULE_HASH = Rule("#NNN.", r"^#\s*(?P<n>\d{1,6})\.?(?:\s+(?P<t>.{1,100}))?$")

_SPECIAL = re.compile(
    r"^(?:prologue|epilogue|afterword|side\s*story|프롤로그|에필로그|외전|번외|후기|작가의\s*말"
    r"|序章|终章|終章|番外)(?:\s*[:.\-–—]\s*.{0,40}|\s*\d{1,4}\s*화?\.?)?$", re.IGNORECASE)

_AUTO_RULES = [
    Rule("Chapter N", rf"^(?:chapter|chap\.?|ch\.?)\s*(?P<n>\d{{1,6}}){_SEP}(?P<t>.{{0,100}})$"),
    RULE_KOREAN,
    Rule("제N장", r"^제\s*(?P<n>\d{1,6})\s*[장회편부]\.?(?:\s+(?P<t>.{1,100}))?$"),
    Rule("第N章", rf"^第\s*(?P<n>[{_CJK}\d]+)\s*[章话話回节節卷集]\.?(?:\s*(?P<t>.{{1,100}}))?$", "cjk"),
    RULE_HASH,
    Rule("Episode N", rf"^(?:episode|ep\.?)\s*(?P<n>\d{{1,6}}){_SEP}(?P<t>.{{0,100}})$"),
    Rule("Chapter <roman>", rf"^chapter\s+(?P<n>[IVXLCDM]+){_SEP}(?P<t>.{{0,100}})$", "roman"),
]
# Loose patterns, only tried when nothing above fits.
_AUTO_FALLBACK = [
    Rule("N. Title", r"^(?P<n>\d{1,5})\.(?:\s+(?P<t>.{1,60}))?$", sequential=True),
    RULE_NUMERIC,
]


def _flex(s: str) -> str:
    return "".join(r"\s*" if part.isspace() else re.escape(part)
                   for part in re.split(r"(\s+)", s) if part)


def rule_from_example(example: str) -> Rule | None:
    """Generalise one pasted heading (e.g. ``Chapter 12: Title``) into a Rule."""
    line = next((ln.strip() for ln in example.splitlines() if ln.strip()), "")
    if not line:
        return None
    tag = re.match(r"^[\[\(【][^\]\)】]*[\]\)】]", line)
    if tag and not re.search(r"\d", tag.group()):
        return Rule(f"tag “{tag.group()}”",
                    "^" + re.escape(tag.group()) + r"\s*(?P<t>.{0,100})$", numtype=None)
    numtype = "int"
    m = re.search(r"\d+", line)
    if not m:
        numtype, m = "cjk", re.search(rf"(?<=第)[{_CJK}]+", line)
    if not m:
        numtype = "roman"
        m = re.search(r"(?:^|(?<=\s))[IVXLCDM]+(?=$|[\s:.\-])", line)
        if m and (_roman_to_int(m.group()) is None or (m.start() == 0 and m.end() < len(line))):
            m = None
    if not m:
        return Rule(f"literal line “{line}”", "^" + _flex(line) + "$", numtype=None)

    prefix, rest = line[:m.start()].rstrip(), line[m.end():]
    sm = re.match(rf"[{_COUNTERS}]?[^\w\s]{{0,3}}", rest)
    suffix, tail = sm.group(0), rest[sm.end():].strip()
    num_pat = {"int": r"\d{1,6}", "cjk": rf"[{_CJK}\d]+", "roman": r"[IVXLCDM]+"}[numtype]
    soft = ":.-–—,"
    anchored = bool(prefix) or any(c not in soft for c in suffix)
    if anchored:  # trailing punctuation may differ or be missing on other headings
        core = suffix.rstrip(soft)
        suf_pat = re.escape(core)
        if core and not core[-1].isalnum():
            title_pat = r"\s*(?P<t>.{0,100})"
        else:
            title_pat = r"(?:\s*[:.\-–—,]+\s*|\s+|$)(?P<t>.{0,100})"
    else:
        suf_pat = re.escape(suffix)
        title_pat = r"(?:\s+(?P<t>.{1,100}))?" if tail else ""
    pattern = "^" + _flex(prefix) + r"\s*" + f"(?P<n>{num_pat})" + suf_pat + title_pat + "$"
    shape = f"{prefix}{' ' if prefix and line[m.start() - 1].isspace() else ''}{{n}}{suffix}"
    return Rule(f"example “{shape}”", pattern, numtype, sequential=not anchored)


# --------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------

@dataclass
class Chapter:
    index: int            # 0 = text before the first heading
    number: int | None
    heading: str
    subtitle: str
    lines: list[str]

    def title(self, template: str | None) -> str:
        if template and self.number is not None:
            base = template.replace("{n}", str(self.number))
            return f"{base} {self.subtitle}".strip()
        return self.heading


def _best_chain(nums: list[int]) -> set[int]:
    """Indexes of the longest rising run (steps of 1..3, +1 preferred)."""
    n = len(nums)
    best, prev = [1] * n, [-1] * n
    for i in range(n):
        for j in range(max(0, i - 200), i):
            d = nums[i] - nums[j]
            if 1 <= d <= 3:
                score = best[j] + (2 if d == 1 else 1)
                if score > best[i]:
                    best[i], prev[i] = score, j
    if n == 0:
        return set()
    i = max(range(n), key=lambda k: best[k])
    if best[i] == 1:
        return {0} if n == 1 else set()
    keep = set()
    while i != -1:
        keep.add(i)
        i = prev[i]
    return keep


def find_headings(lines: list[str], rule) -> list[tuple[int, int | None, str, str]]:
    """Return [(line_index, number, subtitle, heading_text)]."""
    hits = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or len(s) > 120:
            continue
        r = rule.match(s)
        if r:
            hits.append((i, r[0], r[1], r[2] if len(r) > 2 else s))
        elif _SPECIAL.match(s):
            hits.append((i, None, "", s))
    if rule.sequential:
        numbered = [k for k, h in enumerate(hits) if h[1] is not None]
        keep = {numbered[k] for k in _best_chain([hits[k][1] for k in numbered])}
        hits = [h for k, h in enumerate(hits) if h[1] is None or k in keep]
    # Table-of-contents entries: a number that shows up again later, with almost no text under it.
    ident = lambda h: h[1] if h[1] is not None else h[3]
    later = {ident(h): k for k, h in enumerate(hits)}
    kept = []
    for k, h in enumerate(hits):
        if later[ident(h)] > k:
            end = hits[k + 1][0] if k + 1 < len(hits) else len(lines)
            if sum(1 for ln in lines[h[0] + 1:end] if ln.strip()) <= 6:
                continue
        kept.append(h)
    hits = kept
    # Repeated headings (e.g. PDF running headers) stay in the body.
    out, last = [], object()
    for h in hits:
        if h[1] is not None and h[1] == last:
            continue
        out.append(h)
        last = h[1]
    return out


def _score(hits) -> int:
    nums = [h[1] for h in hits if h[1] is not None]
    return sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1) + (1 if nums else 0)


def detect_rule(lines: list[str]):
    """Pick the built-in pattern that yields the best consecutive numbering."""
    for tier in (_AUTO_RULES, _AUTO_FALLBACK):
        scored = [(_score(find_headings(lines, r)), r) for r in tier]
        score, rule = max(scored, key=lambda x: x[0])
        if score >= 3:
            return rule
    return None


def split_text(text: str, rule) -> list[Chapter]:
    lines = text.split("\n")
    hits = find_headings(lines, rule) if rule else []
    if not hits:
        return []
    chapters = []
    front = lines[:hits[0][0]]
    if any(ln.strip() for ln in front):
        chapters.append(Chapter(0, None, "Front Matter", "", front))
    for seq, (start, number, subtitle, heading) in enumerate(hits, 1):
        end = hits[seq][0] if seq < len(hits) else len(lines)
        if rule.numtype is None and not _SPECIAL.match(heading):
            number = seq
            if not re.search(r"\w", heading):  # bare separators such as "***"
                heading = f"Chapter {seq}"
        chapters.append(Chapter(seq, number, heading, subtitle, lines[start + 1:end]))
    return chapters


def apply_spacing(lines: list[str], mode: str) -> list[str]:
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if mode == "remove":
        return [ln for ln in lines if ln.strip()]
    if mode == "double":
        out: list[str] = []
        for ln in lines:
            if ln.strip():
                out += [ln, ""]
        return out[:-1]
    return lines


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def safe_filename(name: str, limit: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:limit].rstrip(" .") or "untitled"


def _lang(sample: str) -> str:
    if re.search(r"[\uac00-\ud7a3]", sample):
        return "ko"
    if re.search(r"[\u3040-\u30ff]", sample):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", sample):
        return "zh"
    return "en"


def _sample(sections: list[Section]) -> str:
    return " ".join(t + " " + " ".join(body[:20]) for t, body in sections[:5])


def write_txt(path: Path, sections: list[Section], compact: bool = False) -> None:
    gap = "\n" if compact else "\n\n"
    parts = [(title + gap + "\n".join(body)).rstrip() for title, body in sections]
    Path(path).write_text((gap + "\n").join(parts) + "\n", encoding="utf-8")


_EPUB_CSS = """body { margin: 5%; line-height: 1.7; }
h2 { margin: 1.5em 0 1.2em; text-align: center; }
p { margin: 0; text-indent: 1em; }
p.gap { text-indent: 0; }
"""


def write_epub(path: Path, book_title: str, sections: list[Section]) -> None:
    lang = _lang(_sample(sections))
    esc = lambda s: html.escape(s, quote=False)
    book_id = f"urn:uuid:{uuid.uuid4()}"
    names = [f"ch{i:05d}.xhtml" for i in range(1, len(sections) + 1)]

    def chapter_doc(title: str, body: list[str]) -> str:
        paras, blank = [], False
        for ln in body:
            if ln.strip():
                paras.append(f"<p>{esc(ln.strip())}</p>")
                blank = False
            elif not blank:
                paras.append('<p class="gap">&#160;</p>')
                blank = True
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}">\n'
            f'<head><meta charset="utf-8"/><title>{esc(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body>\n<h2>{esc(title)}</h2>\n' + "\n".join(paras) + "\n</body>\n</html>\n")

    manifest = "\n".join(
        f'    <item id="c{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(names, 1))
    spine = "\n".join(f'    <itemref idref="c{i}"/>' for i in range(1, len(names) + 1))
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{esc(book_title)}</dc:title>
    <dc:language>{lang}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>
"""
    nav_items = "\n".join(f'      <li><a href="{n}">{esc(t)}</a></li>'
                          for n, (t, _) in zip(names, sections))
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><meta charset="utf-8"/><title>{esc(book_title)}</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{esc(book_title)}</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
</body>
</html>
"""
    nav_points = "\n".join(
        f'    <navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(t)}</text></navLabel>'
        f'<content src="{n}"/></navPoint>'
        for i, (n, (t, _)) in enumerate(zip(names, sections), 1))
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_id}"/></head>
  <docTitle><text>{esc(book_title)}</text></docTitle>
  <navMap>
{nav_points}
  </navMap>
</ncx>
"""
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '  <rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/style.css", _EPUB_CSS)
        for name, (title, body) in zip(names, sections):
            z.writestr(f"OEBPS/{name}", chapter_doc(title, body))


def _pdf_font_names() -> tuple[str, str]:
    """Register a Unicode (CJK-capable) font once; fall back to Helvetica."""
    global _pdf_fonts
    with _FONT_LOCK:
        if _pdf_fonts:
            return _pdf_fonts
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        candidates = [
            (os.path.join(win, "malgun.ttf"), os.path.join(win, "malgunbd.ttf")),
            (os.path.join(win, "msyh.ttc"), os.path.join(win, "msyhbd.ttc")),
            ("/System/Library/Fonts/AppleSDGothicNeo.ttc", None),
            ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
             "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ]
        _pdf_fonts = ("Helvetica", "Helvetica-Bold")
        for regular, bold in candidates:
            if not os.path.exists(regular):
                continue
            try:
                pdfmetrics.registerFont(TTFont("NPBody", regular))
                bold_name = "NPBody"
                if bold and os.path.exists(bold):
                    pdfmetrics.registerFont(TTFont("NPBold", bold))
                    bold_name = "NPBold"
                _pdf_fonts = ("NPBody", bold_name)
                break
            except Exception:
                continue
        return _pdf_fonts


@dataclass
class OutputOptions:
    compact: bool = False        # TXT: no blank lines at all
    page_numbers: bool = True    # PDF: page number in the footer
    toc: bool = False            # PDF: table of contents page(s) up front
    toc_numbers: bool = True     # PDF: page numbers inside the TOC
    start_page: int = 1          # PDF: number printed on the first page


def write_pdf(path: Path, book_title: str, sections: list[Section],
              opts: OutputOptions | None = None) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.tableofcontents import TableOfContents

    opts = opts or OutputOptions()
    regular, bold = _pdf_font_names()
    wrap = "CJK" if _lang(_sample(sections)) != "en" else None
    body_style = ParagraphStyle("body", fontName=regular, fontSize=11, leading=18, wordWrap=wrap)
    head_style = ParagraphStyle("head", fontName=bold, fontSize=17, leading=24,
                                spaceAfter=16, wordWrap=wrap)
    use_toc = opts.toc and len(sections) > 1
    offset = opts.start_page - 1

    class Doc(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            label = getattr(flowable, "_bookmark", None)
            if label:
                key = flowable._key
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(label, key, 0, False)
                if use_toc:
                    self.notify("TOCEntry", (0, xml_escape(label), self.page + offset, key))

    def footer(canvas, doc):
        if opts.page_numbers:
            canvas.saveState()
            canvas.setFont(regular, 9)
            canvas.drawCentredString(A4[0] / 2, 11 * mm, str(canvas.getPageNumber() + offset))
            canvas.restoreState()

    story = []
    if use_toc:
        toc = TableOfContents()
        toc.levelStyles = [ParagraphStyle("toc", fontName=regular, fontSize=11, leading=17,
                                          wordWrap=wrap)]
        toc.dotsMinLevel = 0 if opts.toc_numbers else -1
        if not opts.toc_numbers:
            toc.formatter = lambda page: ""
        story += [Paragraph("Table of Contents", head_style), toc, PageBreak()]
    for i, (title, body) in enumerate(sections):
        if i:
            story.append(PageBreak())
        head = Paragraph(xml_escape(title), head_style)
        head._bookmark, head._key = title, f"ch{i}"
        story.append(head)
        blank = False
        for ln in body:
            if ln.strip():
                story.append(Paragraph(xml_escape(ln.strip()), body_style))
                blank = False
            elif not blank:
                story.append(Spacer(1, 9))
                blank = True
    doc = Doc(str(path), pagesize=A4, title=book_title, leftMargin=22 * mm, rightMargin=22 * mm,
              topMargin=20 * mm, bottomMargin=20 * mm)
    if use_toc:  # needs extra passes so the TOC knows every chapter's page
        doc.multiBuild(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)


def write_master_csv(path: Path, source: str, rows: list[tuple[Chapter, str, list[str]]]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Index", "Chapter Number", "Title", "Characters", "Content"])
        for ch, title, body in rows:
            content = "\n".join(body)
            w.writerow([source, ch.index, "" if ch.number is None else ch.number,
                        title, len(content), content])


_WRITERS = {
    "txt": lambda p, title, secs, opts: write_txt(p, secs, opts.compact),
    "pdf": lambda p, title, secs, opts: write_pdf(p, title, secs, opts),
    "epub": lambda p, title, secs, opts: write_epub(p, title, secs),
}


# --------------------------------------------------------------------------
# Naver Series
# --------------------------------------------------------------------------

def fetch_naver_titles(url: str, log: LogFn) -> list[tuple[str, str]]:
    """Download a Naver Series episode list as [(episode label, title)]."""
    import time

    import requests
    m = re.search(r"productNo=(\d+)", url)
    if not m:
        raise ValueError("Naver Series link must contain productNo=… "
                         "(e.g. https://series.naver.com/novel/detail.series?productNo=123456)")
    product = m.group(1)
    descending = bool(re.search(r"sortOrder=DESC", url, re.IGNORECASE))
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Referer": f"https://series.naver.com/novel/detail.series?productNo={product}",
    })
    entries: list[tuple[str, str]] = []
    seen = set()
    for page in range(1, 5000):
        r = session.get("https://series.naver.com/novel/volumeList.series",
                        params={"productNo": product, "sortOrder": "ASC", "page": page},
                        timeout=20)
        r.raise_for_status()
        try:
            items = r.json().get("resultData") or []
        except ValueError:
            raise ValueError("Naver Series did not return an episode list "
                             "(the title may need a login, or the request was blocked).")
        fresh = 0
        for item in items:
            label = (item.get("volumnNameText") or "").strip()
            title = (item.get("subProductName") or "").strip()
            key = item.get("productNo") or (label, title)
            if key not in seen:
                seen.add(key)
                entries.append((label, title))
                fresh += 1
        if not fresh:
            break
        log(f"Naver Series: page {page} → {len(entries)} episodes so far")
        time.sleep(0.2)
    if not entries:
        raise ValueError("Naver Series returned no episodes for that link.")
    if descending:
        entries.reverse()
    log(f"Naver Series: {len(entries)} episode titles loaded.")
    return entries


# --------------------------------------------------------------------------
# Batch processing
# --------------------------------------------------------------------------

@dataclass
class Settings:
    formats: set[str]              # any of: txt, pdf, epub, csv
    merged: bool = True            # one merged file instead of one file per chapter
    spacing: str = "standard"      # standard | double | remove
    mode: str = "auto"             # auto | numeric | korean | hash | naver | none
    example: str = ""              # example heading for auto mode (blank = detect)
    naver_url: str = ""
    template: str | None = None    # e.g. "Ch.{n}"; None = keep original headings
    workers: int = DEFAULT_WORKERS
    page_numbers: bool = True
    toc: bool = False
    toc_numbers: bool = True
    start_page: int = 1
    keep_layout: bool = False      # EPUB → PDF through WeasyPrint (images + CSS)

    def output_options(self) -> OutputOptions:
        return OutputOptions(self.spacing == "remove", self.page_numbers, self.toc,
                             self.toc_numbers, self.start_page)


def natural_key(p) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def collect_files(paths) -> list[Path]:
    """Expand folders and keep supported files, de-duplicated, in natural order."""
    found: dict[str, Path] = {}
    for p in map(Path, paths):
        items = sorted(p.rglob("*"), key=natural_key) if p.is_dir() else [p]
        for f in items:
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXT:
                found.setdefault(str(f.resolve()).lower(), f)
    return list(found.values())


def _resolve_rule(settings: Settings, lines: list[str], naver_rule, tag: str, log: LogFn):
    if settings.mode == "none":
        return None
    if settings.mode == "numeric":
        return RULE_NUMERIC
    if settings.mode == "korean":
        return RULE_KOREAN
    if settings.mode == "hash":
        return RULE_HASH
    if settings.mode == "naver" and naver_rule:
        found = len(find_headings(lines, naver_rule))
        if found >= 2:
            if found < naver_rule.count:
                log(f"{tag} Matched {found} of {naver_rule.count} Naver episodes in this file.")
            return naver_rule
        log(f"{tag} Naver titles not found in the text; falling back to auto-detect.")
    elif settings.mode == "auto" and settings.example.strip():
        return rule_from_example(settings.example)
    return detect_rule(lines)


def _source_chapters(path: Path, text: str) -> list[Chapter]:
    """Chapters from the file's own structure: EPUB spine documents, else one block."""
    if path.suffix.lower() == ".epub":
        out = []
        for i, (title, body) in enumerate(read_epub_sections(path), 1):
            lines = _clean(body).split("\n")
            if title and lines and lines[0].strip() == title.strip():
                lines = lines[1:]
            out.append(Chapter(i, None, title or f"Section {i}", "", lines))
        if out:
            return out
    return [Chapter(1, None, path.stem, "", text.split("\n"))]


def _layout_pdf(path: Path, target: Path, title: str, sections: list[Section],
                settings: Settings, tag: str, log: LogFn) -> None:
    try:
        import epub_layout_pdf
        epub_layout_pdf.convert(path, target, settings, log=lambda m: log(f"{tag} {m}"))
    except Exception as e:
        log(f"{tag} Layout-preserving PDF failed ({type(e).__name__}: {e}); using text PDF.")
        write_pdf(target, title, sections, settings.output_options())


def process_file(path: Path, settings: Settings, naver_rule, io_pool: ThreadPoolExecutor,
                 log: LogFn, progress: ProgressFn, cancel: threading.Event | None = None) -> dict:
    path = Path(path)
    tag = f"[{path.name}]"
    log(f"{tag} Reading…")
    text = read_text(path)
    if not text.strip():
        raise ValueError("no readable text (empty file, or a scanned/image-only PDF)")
    progress(0.1)

    rule = _resolve_rule(settings, text.split("\n"), naver_rule, tag, log)
    chapters = split_text(text, rule) if rule else []
    real = [c for c in chapters if c.index > 0]
    if chapters:
        log(f"{tag} {len(real)} chapters found using {rule.name}.")
        nums = [c.number for c in real if c.number is not None]
        gaps = [f"{a}→{b}" for a, b in zip(nums, nums[1:]) if b != a + 1]
        if gaps:
            more = "…" if len(gaps) > 8 else ""
            log(f"{tag} Note: numbering jumps at {', '.join(gaps[:8])}{more}")
    else:
        chapters = real = _source_chapters(path, text)
        if settings.mode != "none":
            log(f"{tag} No chapter headings matched; keeping the source structure "
                f"({len(chapters)} section(s)). Paste an example heading to split it.")

    rows = [(c, c.title(settings.template), apply_spacing(c.lines, settings.spacing))
            for c in chapters]
    sections = [(title, body) for _, title, body in rows]
    progress(0.2)

    out_dir = path.parent / f"{path.stem}_Converted"
    out_dir.mkdir(exist_ok=True)
    opts = settings.output_options()
    width = max(3, len(str(len(chapters))))
    jobs: list[Callable[[], None]] = []
    for fmt in ("txt", "pdf", "epub"):
        if fmt not in settings.formats:
            continue
        writer = _WRITERS[fmt]
        if settings.merged:
            target = out_dir / f"{path.stem}.{fmt}"
            if fmt == "pdf" and settings.keep_layout and path.suffix.lower() == ".epub":
                jobs.append(lambda t=target: _layout_pdf(path, t, path.stem, sections,
                                                         settings, tag, log))
            else:
                jobs.append(lambda w=writer, t=target: w(t, path.stem, sections, opts))
        else:
            fmt_dir = out_dir / fmt.upper()
            fmt_dir.mkdir(exist_ok=True)
            for ch, title, body in rows:
                target = fmt_dir / f"{ch.index:0{width}d} {safe_filename(title)}.{fmt}"
                jobs.append(lambda w=writer, t=target, ti=title, b=body:
                            w(t, ti, [(ti, b)], opts))
    if "csv" in settings.formats:
        jobs.append(lambda: write_master_csv(out_dir / f"{path.stem}_master.csv", path.name, rows))

    def guarded(job):
        if cancel is None or not cancel.is_set():
            job()

    failures = 0
    futures = [io_pool.submit(guarded, job) for job in jobs]
    for done, fut in enumerate(as_completed(futures), 1):
        try:
            fut.result()
        except Exception as e:  # keep going; one bad chapter shouldn't sink the file
            failures += 1
            if failures <= 5:
                log(f"{tag} ERROR writing a file: {type(e).__name__}: {e}")
        progress(0.2 + 0.8 * done / len(futures))
    progress(1.0)
    if cancel is not None and cancel.is_set():
        log(f"{tag} Cancelled.")
    else:
        log(f"{tag} Done → {out_dir}" + (f"  ({failures} write errors)" if failures else ""))
    return {"file": path, "chapters": len(real), "outputs": len(jobs) - failures,
            "failures": failures, "out_dir": out_dir}


def process_batch(paths: list[Path], settings: Settings, log: LogFn, progress: ProgressFn,
                  cancel: threading.Event | None = None) -> list[dict]:
    """Process every file in parallel. Per-file errors are logged, not raised."""
    naver_rule = None
    if settings.mode == "naver":
        naver_rule = TitleListRule(fetch_naver_titles(settings.naver_url, log))

    fractions = {i: 0.0 for i in range(len(paths))}
    lock = threading.Lock()

    def file_progress(i: int) -> ProgressFn:
        def update(frac: float) -> None:
            with lock:
                fractions[i] = frac
                overall = sum(fractions.values()) / len(fractions)
            progress(overall)
        return update

    def run(i: int, p: Path) -> dict:
        if cancel is not None and cancel.is_set():
            return {"file": Path(p), "error": "cancelled"}
        return process_file(p, settings, naver_rule, io_pool, log, file_progress(i), cancel)

    results = []
    workers = max(1, settings.workers)
    log(f"Processing {len(paths)} file(s) with {workers} worker threads…")
    # Separate pools: file tasks block on their write tasks, so sharing one could deadlock.
    with ThreadPoolExecutor(min(workers, len(paths)), thread_name_prefix="file") as file_pool, \
            ThreadPoolExecutor(workers, thread_name_prefix="write") as io_pool:
        futures = {file_pool.submit(run, i, p): (i, p) for i, p in enumerate(paths)}
        for fut in as_completed(futures):
            i, p = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                log(f"[{Path(p).name}] FAILED: {type(e).__name__}: {e}")
                results.append({"file": Path(p), "error": str(e)})
                file_progress(i)(1.0)
    return results


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def _read_for_merge(path: Path) -> list[tuple[str, str]]:
    path = Path(path)
    if path.suffix.lower() == ".epub":
        parts = read_epub_sections(path)
        many = len(parts) > 1
        return [(title or (f"{path.stem} – {i}" if many else path.stem), _clean(text))
                for i, (title, text) in enumerate(parts, 1)]
    return [(path.stem, read_text(path))]


def _retitle(name: str, template: str) -> str:
    m = re.search(r"\d+", name)
    if not m:
        return f"{template.replace('{n}', '')} {name}".strip()
    rest = re.sub(r"^.*?\d+\s*[화편장권]?\.?\s*-?\s*", "", name, count=1).strip()
    return f"{template.replace('{n}', str(int(m.group())))} {rest}".strip()


def merge_files(paths: list[Path], out_path: Path, settings: Settings, log: LogFn,
                progress: ProgressFn) -> int:
    """Merge TXT/PDF/EPUB files (in the given order) into one TXT, PDF or EPUB."""
    out_path = Path(out_path)
    fmt = out_path.suffix.lower().lstrip(".")
    if fmt not in _WRITERS:
        raise ValueError("Output file must end in .txt, .pdf or .epub")
    workers = max(1, settings.workers)
    log(f"Merging {len(paths)} files → {out_path.name} ({workers} reader threads)…")
    loaded: list = [None] * len(paths)
    with ThreadPoolExecutor(workers, thread_name_prefix="read") as pool:
        futures = {pool.submit(_read_for_merge, p): i for i, p in enumerate(paths)}
        for done, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            try:
                loaded[i] = fut.result()
            except Exception as e:
                log(f"[{Path(paths[i]).name}] SKIPPED: {type(e).__name__}: {e}")
                loaded[i] = []
            progress(0.7 * done / len(paths))

    norm = lambda s: re.sub(r"\s+", "", s).lower()
    sections: list[Section] = []
    for parts in loaded:
        for title, text in parts:
            body = apply_spacing(text.split("\n"), settings.spacing)
            if not body:
                continue
            # Reuse the file's own first line as the heading instead of duplicating it.
            if len(norm(body[0])) >= 2 and len(body[0]) <= 120 and \
                    (norm(body[0]) in norm(title) or norm(title) in norm(body[0])):
                title = body[0].strip()
                body = apply_spacing(body[1:], settings.spacing)
            if settings.template:
                title = _retitle(title, settings.template)
            sections.append((title, body))
    if not sections:
        raise ValueError("Nothing to merge: no readable text in the selected files.")
    log(f"Writing {len(sections)} sections…")
    _WRITERS[fmt](out_path, out_path.stem, sections, settings.output_options())
    progress(1.0)
    log(f"Merged {len(sections)} sections → {out_path}")
    return len(sections)
