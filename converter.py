import sys
import os
import base64
import logging
import re
import tempfile
import html
import json
from urllib.parse import unquote
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QTextEdit, QProgressBar, QCheckBox, QGroupBox, QFormLayout, QSpinBox)
from PySide6.QtCore import Qt, Signal, QObject, QThread, QSize
from PySide6.QtGui import QScreen

# --- GTK/MSYS2 DLLs for WeasyPrint (PDF) ---
gtk_folder = os.environ.get('GTK_FOLDER', '')
msys2_bin_candidates = [
    os.path.join(gtk_folder, 'bin') if gtk_folder else '',
    r'C:\msys64\mingw64\bin',
    r'C:\msys64\ucrt64\bin',
    r'D:\a\_temp\msys64\mingw64\bin',
]
msys2_bin = None
for candidate in msys2_bin_candidates:
    if candidate and os.path.exists(candidate):
        msys2_bin = candidate
        break
if msys2_bin:
    os.environ['PATH'] = msys2_bin + os.pathsep + os.environ.get('PATH', '')
def _ensure_fontconfig():
    if os.environ.get("FONTCONFIG_FILE") and os.environ.get("FONTCONFIG_PATH"):
        return
    temp_dir = tempfile.mkdtemp(prefix="fontconfig_")
    conf_path = os.path.join(temp_dir, "fonts.conf")
    if not os.path.exists(conf_path):
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

from weasyprint import HTML
import ebooklib
from ebooklib import epub

# Custom handler to redirect logging to a Qt widget
class QtLogHandler(logging.Handler, QObject):
    new_log_record = Signal(str)

    def __init__(self):
        super().__init__()
        QObject.__init__(self)

    def emit(self, record):
        msg = self.format(record)
        self.new_log_record.emit(msg)

class NoScrollSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()

class FileConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Converter")
        self.setAcceptDrops(True)
        self.settings_file = "config.json"
        self.current_settings = self.load_settings()

        # Set size based on screen ratio (e.g., 40% width, 50% height)
        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            width = int(screen_geometry.width() * 0.4)
            height = int(screen_geometry.height() * 0.5)
            self.resize(width, height)
        else:
            self.resize(500, 400) # Fallback

        self.layout = QVBoxLayout(self)

        self.file_label = QLabel("No file selected", self)
        self.file_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.file_label)

        self.select_button = QPushButton("Select File", self)
        self.select_button.clicked.connect(self.on_select_file)
        self.layout.addWidget(self.select_button)

        self.convert_button = QPushButton("Convert to PDF", self)
        self.convert_button.clicked.connect(self.on_convert_file)
        self.convert_button.setEnabled(False)
        self.layout.addWidget(self.convert_button)
        
        # Settings Group
        self.settings_group = QGroupBox("PDF Settings")
        self.settings_layout = QVBoxLayout()
        
        self.page_numbers_check = QCheckBox("Add Page Numbers to Footer")
        self.page_numbers_check.setChecked(self.current_settings.get("page_numbers", True))
        self.page_numbers_check.stateChanged.connect(self.save_settings)
        self.settings_layout.addWidget(self.page_numbers_check)
        
        self.toc_check = QCheckBox("Generate Table of Contents")
        self.toc_check.setChecked(self.current_settings.get("toc", True))
        self.toc_check.stateChanged.connect(self.toggle_toc_options)
        self.settings_layout.addWidget(self.toc_check)

        self.toc_numbers_check = QCheckBox("Add Page Numbers to TOC")
        self.toc_numbers_check.setChecked(self.current_settings.get("toc_numbers", True))
        self.toc_numbers_check.setEnabled(self.toc_check.isChecked())
        self.toc_numbers_check.stateChanged.connect(self.save_settings)
        # Indent the sub-option slightly for visual hierarchy if possible, or just add it
        self.settings_layout.addWidget(self.toc_numbers_check)

        # TOC Start Page
        self.toc_start_layout = QFormLayout()
        self.toc_start_page_spin = NoScrollSpinBox()
        self.toc_start_page_spin.setFixedWidth(80)
        self.toc_start_page_spin.setRange(1, 9999)
        self.toc_start_page_spin.setValue(self.current_settings.get("toc_start_page", 1))
        self.toc_start_page_spin.setEnabled(self.toc_check.isChecked())
        self.toc_start_page_spin.valueChanged.connect(self.save_settings)
        self.toc_start_label = QLabel("Start Page Number:")
        self.toc_start_label.setEnabled(self.toc_check.isChecked())
        self.toc_start_layout.addRow(self.toc_start_label, self.toc_start_page_spin)
        self.settings_layout.addLayout(self.toc_start_layout)

        self.settings_group.setLayout(self.settings_layout)
        self.layout.addWidget(self.settings_group)
        
        # Add progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        self.layout.addWidget(self.progress_bar)

        # Add log viewer
        self.log_viewer = QTextEdit(self)
        self.log_viewer.setReadOnly(True)
        self.layout.addWidget(self.log_viewer)

        self.file_path = None

        # Setup logging
        self.log_handler = QtLogHandler()
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.log_handler.new_log_record.connect(self.log_viewer.append)
        
        # Add the custom handler to the root logger
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

        self.worker_thread = None
        self.worker = None

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed to load settings: {e}")
        return {"page_numbers": True, "toc": False, "toc_numbers": False, "toc_start_page": 1}

    def save_settings(self):
        settings = {
            "page_numbers": self.page_numbers_check.isChecked(),
            "toc": self.toc_check.isChecked(),
            "toc_numbers": self.toc_numbers_check.isChecked(),
            "toc_start_page": self.toc_start_page_spin.value()
        }
        try:
            with open(self.settings_file, "w") as f:
                json.dump(settings, f)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")

    def toggle_toc_options(self, state):
        self.toc_numbers_check.setEnabled(self.toc_check.isChecked())
        self.toc_start_page_spin.setEnabled(self.toc_check.isChecked())
        self.toc_start_label.setEnabled(self.toc_check.isChecked())
        self.save_settings()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if file_path.lower().endswith(('.epub', '.txt')):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if file_path.lower().endswith(('.epub', '.txt')):
                        self.file_path = file_path
                        self.file_label.setText(os.path.basename(self.file_path))
                        self.convert_button.setEnabled(True)
                        logging.info(f"Selected file via drag & drop: {self.file_path}")
                        return

    def on_select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "EPUB and TXT files (*.epub *.txt)")
        if file_path:
            self.file_path = file_path
            self.file_label.setText(os.path.basename(self.file_path))
            self.convert_button.setEnabled(True)
            logging.info(f"Selected file: {self.file_path}")

    def on_convert_file(self):
        if self.file_path:
            output_path = os.path.splitext(self.file_path)[0] + ".pdf"
            self.convert_button.setEnabled(False)
            self.select_button.setEnabled(False)
            logging.info(f"Starting conversion to {output_path}")
            
            settings = {
                'page_numbers': self.page_numbers_check.isChecked(),
                'toc': self.toc_check.isChecked(),
                'toc_numbers': self.toc_numbers_check.isChecked(),
                'toc_start_page': self.toc_start_page_spin.value()
            }
            
            self.worker_thread = QThread()
            self.worker = ConversionWorker(self.file_path, output_path, settings, self)
            self.worker.moveToThread(self.worker_thread)
            self.worker_thread.started.connect(self.worker.run)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.error.connect(self.on_worker_error)
            self.worker.progress.connect(self.progress_bar.setValue)
            self.worker_thread.start()

    def convert_txt_to_pdf(self, input_path, output_path, settings, progress_callback=None):
        if progress_callback: progress_callback(10)
        logging.info(f"Reading TXT file: {input_path}")
        with open(input_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if progress_callback: progress_callback(30)
        logging.info("Generating HTML from TXT content...")
        
        css = ""
        start_page = settings.get('toc_start_page', 1)
        if settings.get('page_numbers'):
            css = f"@page {{ @bottom-center {{ content: counter(page); }} }} body {{ counter-reset: page {start_page - 1}; }}"
            
        html_content = f"<html><head><style>{css}</style></head><body><pre>{content}</pre></body></html>"
        
        if progress_callback: progress_callback(60)
        logging.info("Writing PDF...")
        # Provide a base_url to resolve relative paths for CSS, fonts, etc.
        input_dir = os.path.dirname(os.path.abspath(input_path))
        HTML(string=html_content, base_url=input_dir).write_pdf(output_path)
        if progress_callback: progress_callback(100)
        logging.info("Finished writing PDF.")

    def convert_epub_to_pdf(self, input_path, output_path, settings, progress_callback=None):
        if progress_callback: progress_callback(5)
        logging.info(f"Reading EPUB file: {input_path}")
        book = epub.read_epub(input_path)
        
        # Collect all image items (including those ebooklib might miss if mimetype is unusual like webp)
        images_by_path = {}
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_IMAGE or (item.media_type and item.media_type.startswith('image/')):
                images_by_path[item.get_name().replace('\\', '/')] = item
                
        logging.info(f"Found {len(images_by_path)} total images in EPUB.")

        # Determine TOC/Nav items to exclude from main content if we generate our own
        # But actually, we usually want to keep them unless the user explicitly wants to replace them.
        # For now, we'll just prepend our generated TOC if requested.

        items_to_process = [book.get_item_with_id(item_id) for item_id, _ in book.spine]
        spine_hrefs = {item.get_name() for item in items_to_process if item}
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if item.get_name() not in spine_hrefs:
                items_to_process.append(item)
                
        # Helper to generate TOC HTML
        def generate_toc_html(book, page_map=None):
            toc_html = "<html><head><style>h1 { text-align: center; } ul { list-style-type: none; padding: 0; } li { margin-bottom: 5px; border-bottom: 1px dotted #ccc; } a { text-decoration: none; color: black; display: flex; justify-content: space-between; } .page { font-weight: bold; }</style></head><body><h1>Table of Contents</h1><ul>"
            
            def process_toc_item(toc_item, level=0):
                html_out = ""
                # ebooklib TOC item can be Link or Tuple or Section
                if isinstance(toc_item, tuple) or isinstance(toc_item, list):
                    section = toc_item[0]
                    children = toc_item[1] if len(toc_item) > 1 else []
                    
                    title = section.title if hasattr(section, 'title') else str(section)
                    href = section.href if hasattr(section, 'href') else ""
                    
                    # Clean href (remove anchors)
                    base_href = href.split('#')[0]
                    
                    page_num = ""
                    if settings.get('toc_numbers') and page_map and base_href in page_map:
                        page_num = str(page_map[base_href])
                    
                    indent = level * 20
                    html_out += f"<li style='padding-left: {indent}px'><a href='#'><span>{html.escape(title)}</span> <span class='page'>{page_num}</span></a></li>"
                    
                    for child in children:
                        html_out += process_toc_item(child, level + 1)
                elif isinstance(toc_item, epub.Link):
                    title = toc_item.title
                    href = toc_item.href
                    base_href = href.split('#')[0]
                    
                    page_num = ""
                    if settings.get('toc_numbers') and page_map and base_href in page_map:
                        page_num = str(page_map[base_href])
                        
                    indent = level * 20
                    html_out += f"<li style='padding-left: {indent}px'><a href='#'><span>{html.escape(title)}</span> <span class='page'>{page_num}</span></a></li>"
                return html_out

            for item in book.toc:
                toc_html += process_toc_item(item)
            
            toc_html += "</ul></body></html>"
            return toc_html

        # DRY RUN for TOC size
        toc_page_count = 0
        toc_doc = None
        
        if settings.get('toc'):
            logging.info("Calculating TOC size...")
            dummy_toc_html = generate_toc_html(book)
            
            # Apply start page to TOC if page numbers are on
            toc_css = ""
            start_page = settings.get('toc_start_page', 1)
            if settings.get('page_numbers'):
                 toc_css = f"<style>@page {{ @bottom-center {{ content: counter(page); }} }} body {{ counter-reset: page {start_page - 1}; }}</style>"
            
            # Inject CSS into dummy TOC
            dummy_toc_html = dummy_toc_html.replace("</style>", f"</style>{toc_css}")

            input_dir = os.path.dirname(os.path.abspath(input_path))
            toc_doc = HTML(string=dummy_toc_html, base_url=input_dir).render()
            toc_page_count = len(toc_doc.pages)
            logging.info(f"Estimated TOC length: {toc_page_count} pages")

        total_steps = len(items_to_process) + 2 # items + merge + finalize
        
        documents = []
        
        # Determine the logical starting page for the content
        # Content starts after the TOC.
        # Logical page of content start = Start Page + TOC Length
        start_page = settings.get('toc_start_page', 1)
        current_page = toc_page_count + start_page - 1
        
        chapter_page_map = {}

        if progress_callback:
            progress_callback(10)

        def _data_uri_for_src(src_value):
            if not src_value or src_value.startswith("data:"):
                return None
            
            # Normalize path
            raw = unquote(src_value).replace("\\", "/")
            if raw.startswith("file:///"):
                raw = raw[8:]
            
            # 1. Try exact match
            # Some paths might be absolute if resolved by base_url, we need relative to epub root
            # But here src_value is what's in the HTML.
            
            # If the HTML has src="images/foo.webp", raw is "images/foo.webp".
            if raw in images_by_path:
                item = images_by_path[raw]
                b64 = base64.b64encode(item.get_content()).decode("utf-8")
                return f"data:{item.media_type};base64,{b64}"
            
            # 2. Try match by filename (fallback)
            # This helps if paths are somehow relative or absolute in a way we didn't expect
            filename = os.path.basename(raw)
            for path, item in images_by_path.items():
                if os.path.basename(path) == filename:
                    b64 = base64.b64encode(item.get_content()).decode("utf-8")
                    return f"data:{item.media_type};base64,{b64}"
            
            return None

        styles = ""
        logging.info("Inlining CSS styles...")
        for item in book.get_items_of_type(ebooklib.ITEM_STYLE):
            styles += item.get_content().decode('utf-8', 'ignore')
        # Replace url(...) inside CSS too (covers background images like cover.jpg)
        def replace_css_url(match):
            url_value = match.group(1).strip(' "\'')
            data_uri = _data_uri_for_src(url_value)
            if data_uri:
                logging.info(f"Embedding CSS image for '{url_value}'")
                return f'url("{data_uri}")'
            return match.group(0)
        styles = re.sub(r'url\\(([^)]+)\\)', replace_css_url, styles, flags=re.IGNORECASE)
        
        # Add page numbering CSS if requested
        if settings.get('page_numbers'):
            styles += " @page { @bottom-center { content: counter(page); } } "
        
        logging.info(f"Processing {len(items_to_process)} documents chapter by chapter...")
        for i, doc_item in enumerate(items_to_process):
            if not doc_item: 
                continue
            
            # Record start page for this chapter
            chapter_page_map[doc_item.get_name()] = current_page + 1

            # Update progress
            current_step = i + 1
            if progress_callback:
                # Map steps to 10-90% range
                percent = 10 + int((current_step / len(items_to_process)) * 80)
                progress_callback(percent)

            logging.info(f"Rendering document: {doc_item.get_name()} (Start Page: {current_page + 1})")
            content = doc_item.get_content().decode('utf-8', 'ignore')

            def replace_attr(match):
                attr = match.group(1)
                quote = match.group(2)
                src_value = match.group(3)
                data_uri = _data_uri_for_src(src_value)
                if data_uri:
                    logging.info(f"Embedding image for '{src_value}'")
                    return f'{attr}={quote}{data_uri}{quote}'
                return match.group(0)

            content = re.sub(
                r'(\b(?:src|href|xlink:href)\b)\s*=\s*([\'"])([^\'"]+)\2',
                replace_attr,
                content,
                flags=re.IGNORECASE
            )

            def replace_css_url(match):
                url_value = match.group(1).strip(' "\'')
                data_uri = _data_uri_for_src(url_value)
                if data_uri:
                    logging.info(f"Embedding CSS image for '{url_value}'")
                    return f'url("{data_uri}")'
                return match.group(0)

            content = re.sub(r'url\\(([^)]+)\\)', replace_css_url, content, flags=re.IGNORECASE)
            
            # Combine the content and styles for this chapter
            # Inject counter-reset to ensure page numbers are continuous
            # Use current_page which tracks the logical page count
            page_reset_css = f"body {{ counter-reset: page {current_page}; }}" if settings.get('page_numbers') else ""
            
            chapter_html = f"<html><head><style>{styles} {page_reset_css}</style></head><body>{content}</body></html>"
            
            # Render this chapter to a Document object
            input_dir = os.path.dirname(os.path.abspath(input_path))
            doc = HTML(string=chapter_html, base_url=input_dir).render()
            documents.append(doc)
            
            current_page += len(doc.pages)
            # QApplication.processEvents() # Unsafe in thread

        # Generate Real TOC if requested
        if settings.get('toc'):
            logging.info("Generating final TOC with page numbers...")
            real_toc_html = generate_toc_html(book, chapter_page_map)
            
            # Apply start page CSS to final TOC as well
            start_page = settings.get('toc_start_page', 1)
            toc_css = ""
            if settings.get('page_numbers'):
                 toc_css = f"<style>@page {{ @bottom-center {{ content: counter(page); }} }} body {{ counter-reset: page {start_page - 1}; }}</style>"
            real_toc_html = real_toc_html.replace("</style>", f"</style>{toc_css}")
            
            input_dir = os.path.dirname(os.path.abspath(input_path))
            real_toc_doc = HTML(string=real_toc_html, base_url=input_dir).render()
            
            # Check for size mismatch
            if len(real_toc_doc.pages) != toc_page_count:
                logging.warning(f"TOC size changed from {toc_page_count} to {len(real_toc_doc.pages)}. Page numbers might be slightly off.")
                # Ideally we would re-render everything, but for now just warn.
                # Or we can insert blank pages to match the count?
                # No, just accept the slight shift.
            
            documents.insert(0, real_toc_doc)

        logging.info("All chapters rendered. Merging into a single PDF...")
        if progress_callback: progress_callback(95)
        
        # Get all pages from all documents and write to the final PDF
        all_pages = [page for doc in documents for page in doc.pages]
        logging.info(f"Total pages collected: {len(all_pages)}")
        logging.info("Writing PDF to disk... (This may take some time for large files)")
        
        documents[0].copy(all_pages).write_pdf(output_path)
        
        logging.info("Finished writing PDF.")
        if progress_callback: progress_callback(100)

    def on_worker_finished(self, output_path):
        self.convert_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.progress_bar.setValue(100)
        QMessageBox.information(self, "Success", f"Successfully converted to {output_path}")
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

    def on_worker_error(self, message):
        self.convert_button.setEnabled(True)
        self.select_button.setEnabled(True)
        QMessageBox.critical(self, "Error", message)
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

class ConversionWorker(QObject):
    finished = Signal(str)
    error = Signal(str)
    progress = Signal(int)

    def __init__(self, input_path, output_path, settings, parent):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.settings = settings
        self.parent = parent

    def run(self):
        try:
            self.progress.emit(0)
            if self.input_path.endswith(".txt"):
                self.parent.convert_txt_to_pdf(self.input_path, self.output_path, self.settings, self.progress.emit)
            elif self.input_path.endswith(".epub"):
                self.parent.convert_epub_to_pdf(self.input_path, self.output_path, self.settings, self.progress.emit)
            logging.info(f"Successfully converted to {self.output_path}")
            self.finished.emit(self.output_path)
        except Exception as e:
            logging.error(f"An error occurred: {e}", exc_info=True)
            self.error.emit(f"An error occurred: {e}")



if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = FileConverter()
    window.show()
    sys.exit(app.exec())
