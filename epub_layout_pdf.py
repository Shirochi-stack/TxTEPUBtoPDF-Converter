"""Layout-preserving EPUB → PDF through WeasyPrint (keeps images and the book's CSS).

Optional: needs WeasyPrint plus the GTK/Pango runtime (MSYS2 on Windows). The
engine falls back to its plain-text PDF writer when this module cannot load.
"""
import base64
import html
import os
import re
import tempfile
import threading
import warnings
from urllib.parse import unquote

# --- GTK/MSYS2 DLLs for WeasyPrint (PDF) ---
gtk_folder = os.environ.get('GTK_FOLDER', '')
msys2_bin_candidates = [
    os.path.join(gtk_folder, 'bin') if gtk_folder else '',
    r'C:\msys64\mingw64\bin',
    r'C:\msys64\ucrt64\bin',
    r'D:\a\_temp\msys64\mingw64\bin',
]
for candidate in msys2_bin_candidates:
    if candidate and os.path.exists(candidate):
        os.environ['PATH'] = candidate + os.pathsep + os.environ.get('PATH', '')
        break


def _ensure_fontconfig():
    if os.environ.get("FONTCONFIG_FILE") and os.environ.get("FONTCONFIG_PATH"):
        return
    temp_dir = tempfile.mkdtemp(prefix="fontconfig_")
    conf_path = os.path.join(temp_dir, "fonts.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write("""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>WINDOWSFONTDIR</dir>
  <cachedir>~/.cache/fontconfig</cachedir>
</fontconfig>
""")
    os.environ["FONTCONFIG_FILE"] = conf_path
    os.environ["FONTCONFIG_PATH"] = temp_dir
    os.environ["FC_CONFIG_FILE"] = conf_path


_ensure_fontconfig()

warnings.filterwarnings('ignore', module='ebooklib')

from weasyprint import HTML  # noqa: E402
import ebooklib  # noqa: E402
from ebooklib import epub  # noqa: E402

# WeasyPrint/Pango are not safe to drive from several threads at once.
_LOCK = threading.Lock()


def convert(input_path, output_path, settings, log=lambda msg: None):
    """Render every EPUB document with its own CSS/images and join them into one PDF."""
    with _LOCK:
        _convert(str(input_path), str(output_path), settings, log)


def _convert(input_path, output_path, settings, log):
    page_numbers, want_toc = settings.page_numbers, settings.toc
    toc_numbers, start_page = settings.toc_numbers, settings.start_page
    input_dir = os.path.dirname(os.path.abspath(input_path))

    book = epub.read_epub(input_path)

    # Collect all image items (including ones ebooklib misses, e.g. webp)
    images_by_path = {}
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_IMAGE or (item.media_type and item.media_type.startswith('image/')):
            images_by_path[item.get_name().replace('\\', '/')] = item
    log(f"Layout PDF: {len(images_by_path)} images found in EPUB.")

    items_to_process = [book.get_item_with_id(item_id) for item_id, _ in book.spine]
    spine_hrefs = {item.get_name() for item in items_to_process if item}
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if item.get_name() not in spine_hrefs:
            items_to_process.append(item)
    items_to_process = [item for item in items_to_process if item]

    def toc_entry(title, href, level, page_map):
        page_num = ""
        if toc_numbers and page_map and href.split('#')[0] in page_map:
            page_num = str(page_map[href.split('#')[0]])
        return (f"<li style='padding-left: {level * 20}px'><a href='#'><span>{html.escape(title)}</span>"
                f" <span class='page'>{page_num}</span></a></li>")

    def generate_toc_html(page_map=None):
        toc_css = ""
        if page_numbers:
            toc_css = (f"@page {{ @bottom-center {{ content: counter(page); }} }} "
                       f"body {{ counter-reset: page {start_page - 1}; }}")
        out = ("<html><head><style>h1 { text-align: center; } ul { list-style-type: none; padding: 0; } "
               "li { margin-bottom: 5px; border-bottom: 1px dotted #ccc; } "
               "a { text-decoration: none; color: black; display: flex; justify-content: space-between; } "
               f".page {{ font-weight: bold; }} {toc_css}</style></head><body><h1>Table of Contents</h1><ul>")

        def walk(toc_item, level=0):
            if isinstance(toc_item, (tuple, list)):
                section = toc_item[0]
                children = toc_item[1] if len(toc_item) > 1 else []
                title = section.title if hasattr(section, 'title') else str(section)
                href = section.href if hasattr(section, 'href') else ""
                return toc_entry(title, href or "", level, page_map) + "".join(
                    walk(child, level + 1) for child in children)
            if isinstance(toc_item, epub.Link):
                return toc_entry(toc_item.title, toc_item.href, level, page_map)
            return ""

        return out + "".join(walk(item) for item in book.toc) + "</ul></body></html>"

    toc_page_count = 0
    if want_toc:
        toc_page_count = len(HTML(string=generate_toc_html(), base_url=input_dir).render().pages)
        log(f"Layout PDF: estimated TOC length {toc_page_count} page(s).")

    def data_uri_for_src(src_value):
        if not src_value or src_value.startswith("data:"):
            return None
        raw = unquote(src_value).replace("\\", "/")
        if raw.startswith("file:///"):
            raw = raw[8:]
        item = images_by_path.get(raw)
        if item is None:  # fall back to matching by file name
            filename = os.path.basename(raw)
            item = next((it for path, it in images_by_path.items()
                         if os.path.basename(path) == filename), None)
        if item is None:
            return None
        b64 = base64.b64encode(item.get_content()).decode("utf-8")
        return f"data:{item.media_type};base64,{b64}"

    def replace_css_url(match):
        data_uri = data_uri_for_src(match.group(1).strip(' "\''))
        return f'url("{data_uri}")' if data_uri else match.group(0)

    def replace_attr(match):
        data_uri = data_uri_for_src(match.group(3))
        if data_uri:
            return f'{match.group(1)}={match.group(2)}{data_uri}{match.group(2)}'
        return match.group(0)

    styles = ""
    for item in book.get_items_of_type(ebooklib.ITEM_STYLE):
        styles += item.get_content().decode('utf-8', 'ignore')
    styles = re.sub(r'url\(([^)]+)\)', replace_css_url, styles, flags=re.IGNORECASE)
    if page_numbers:
        styles += " @page { @bottom-center { content: counter(page); } } "

    documents = []
    current_page = toc_page_count + start_page - 1  # content starts after the TOC
    chapter_page_map = {}
    log(f"Layout PDF: rendering {len(items_to_process)} documents…")
    for doc_item in items_to_process:
        chapter_page_map[doc_item.get_name()] = current_page + 1
        content = doc_item.get_content().decode('utf-8', 'ignore')
        content = re.sub(r'(\b(?:src|href|xlink:href)\b)\s*=\s*([\'"])([^\'"]+)\2',
                         replace_attr, content, flags=re.IGNORECASE)
        content = re.sub(r'url\(([^)]+)\)', replace_css_url, content, flags=re.IGNORECASE)
        # counter-reset keeps page numbers continuous across separately rendered chapters
        page_reset_css = f"body {{ counter-reset: page {current_page}; }}" if page_numbers else ""
        chapter_html = f"<html><head><style>{styles} {page_reset_css}</style></head><body>{content}</body></html>"
        doc = HTML(string=chapter_html, base_url=input_dir).render()
        documents.append(doc)
        current_page += len(doc.pages)

    if want_toc:
        real_toc_doc = HTML(string=generate_toc_html(chapter_page_map), base_url=input_dir).render()
        if len(real_toc_doc.pages) != toc_page_count:
            log(f"Layout PDF: TOC grew from {toc_page_count} to {len(real_toc_doc.pages)} pages; "
                "page numbers may be slightly off.")
        documents.insert(0, real_toc_doc)

    if not documents:
        raise ValueError("EPUB has no documents to render")
    all_pages = [page for doc in documents for page in doc.pages]
    log(f"Layout PDF: writing {len(all_pages)} pages…")
    documents[0].copy(all_pages).write_pdf(output_path)
