import sys
import os
import base64
import logging
import re
import tempfile
from urllib.parse import unquote
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QTextEdit)
from PySide6.QtCore import Qt, Signal, QObject, QThread

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

class FileConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Converter")
        self.resize(500, 400)

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
            self.worker_thread = QThread()
            self.worker = ConversionWorker(self.file_path, output_path, self)
            self.worker.moveToThread(self.worker_thread)
            self.worker_thread.started.connect(self.worker.run)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.error.connect(self.on_worker_error)
            self.worker_thread.start()

    def convert_txt_to_pdf(self, input_path, output_path):
        logging.info(f"Reading TXT file: {input_path}")
        with open(input_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        logging.info("Generating HTML from TXT content...")
        html_content = f"<html><body><pre>{content}</pre></body></html>"
        
        logging.info("Writing PDF...")
        # Provide a base_url to resolve relative paths for CSS, fonts, etc.
        input_dir = os.path.dirname(os.path.abspath(input_path))
        HTML(string=html_content, base_url=input_dir).write_pdf(output_path)
        logging.info("Finished writing PDF.")

    def convert_epub_to_pdf(self, input_path, output_path):
        logging.info(f"Reading EPUB file: {input_path}")
        book = epub.read_epub(input_path)
        
        images_by_path = {item.get_name().replace('\\', '/'): item for item in book.get_items_of_type(ebooklib.ITEM_IMAGE)}
        logging.info(f"Found {len(images_by_path)} total images in EPUB manifest.")

        # Process all documents in order and collect them as WeasyPrint Document objects
        documents = []
        
        items_to_process = [book.get_item_with_id(item_id) for item_id, _ in book.spine]
        spine_hrefs = {item.get_name() for item in items_to_process if item}
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            if item.get_name() not in spine_hrefs:
                items_to_process.append(item)

        def _data_uri_for_src(src_value):
            if not src_value or src_value.startswith("data:"):
                return None
            raw = unquote(src_value).replace("\\", "/")
            if raw.startswith("file:///"):
                raw = raw[8:]
            # Find images/ path segment
            m = re.search(r"(images/[^?#]+)", raw, flags=re.IGNORECASE)
            image_key = m.group(1) if m else None
            if image_key and image_key in images_by_path:
                item = images_by_path[image_key]
                b64 = base64.b64encode(item.get_content()).decode("utf-8")
                return f"data:{item.media_type};base64,{b64}"
            if image_key:
                # Fallback by filename
                filename = os.path.basename(image_key)
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
        styles = re.sub(r'url\(([^)]+)\)', replace_css_url, styles, flags=re.IGNORECASE)
        
        logging.info(f"Processing {len(items_to_process)} documents chapter by chapter...")
        for doc_item in items_to_process:
            if not doc_item: 
                continue
            
            logging.info(f"Rendering document: {doc_item.get_name()}")
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

            content = re.sub(r'url\(([^)]+)\)', replace_css_url, content, flags=re.IGNORECASE)
            
            # Combine the content and styles for this chapter
            chapter_html = f"<html><head><style>{styles}</style></head><body>{content}</body></html>"
            
            # Render this chapter to a Document object
            input_dir = os.path.dirname(os.path.abspath(input_path))
            doc = HTML(string=chapter_html, base_url=input_dir).render()
            documents.append(doc)
            QApplication.processEvents() # Keep GUI responsive

        logging.info("All chapters rendered. Merging into a single PDF...")
        # Get all pages from all documents and write to the final PDF
        all_pages = [page for doc in documents for page in doc.pages]
        documents[0].copy(all_pages).write_pdf(output_path)
        
        logging.info("Finished writing PDF.")

    def on_worker_finished(self, output_path):
        self.convert_button.setEnabled(True)
        self.select_button.setEnabled(True)
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

    def __init__(self, input_path, output_path, parent):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.parent = parent

    def run(self):
        try:
            if self.input_path.endswith(".txt"):
                self.parent.convert_txt_to_pdf(self.input_path, self.output_path)
            elif self.input_path.endswith(".epub"):
                self.parent.convert_epub_to_pdf(self.input_path, self.output_path)
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
