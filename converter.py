import json
import os
import sys
import tempfile
import threading
from pathlib import Path

# Windowed (no-console) builds have no stdout/stderr; some libraries write to them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Stray DLL protection (also run earlier by the PyInstaller runtime hook) — before Qt loads.
import dll_guard  # noqa: E402

dll_guard.protect()

from PySide6.QtCore import QPointF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QKeySequence, QPainter, QPalette, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

import engine

APP_NAME = "File Converter"
APP_VERSION = "2.4"
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SETTINGS_FILE = APP_DIR / "config.json"
SETTINGS_VERSION = 2
FILE_FILTER = "Supported files (*.txt *.pdf *.epub);;All files (*)"

STYLE = """
QWidget { background: #1e1e1e; color: #e6e6e6; font-family: "Segoe UI"; font-size: 10pt; }
QLabel#title { font-size: 21pt; font-weight: 700; }
QLabel#heading { font-size: 10.5pt; font-weight: 700; }
QLabel#hint { color: #9a9a9a; font-size: 8.5pt; }
QLabel#accent { color: #f1c40f; font-weight: 700; }
QLabel#naver { color: #e74c3c; font-weight: 700; }
QFrame#panel { background: #2a2a2a; border-radius: 8px; }
QFrame#panel QLabel, QFrame#panel QCheckBox, QFrame#panel QRadioButton { background: transparent; }
QFrame#side { background: #1f2733; border-radius: 8px; }
QFrame#side QLabel, QFrame#side QStackedWidget, QFrame#side QStackedWidget > QWidget { background: transparent; }
QListWidget, QTextEdit { background: #161616; border: 1px solid #333; border-radius: 6px; padding: 4px; }
QListWidget::item:selected { background: #1f6aa5; color: white; }
QLineEdit, QPlainTextEdit { background: #2f2f2f; border: 1px solid #555; border-radius: 5px; padding: 4px 6px; }
QFrame#side QLineEdit, QFrame#side QPlainTextEdit { background: #2f2f2f; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #3b8ed0; }
QPushButton { background: #1f6aa5; color: white; border: none; border-radius: 6px; padding: 8px 16px; }
QPushButton:hover { background: #2a7fc0; }
QPushButton:disabled { background: #3a3a3a; color: #777; }
QPushButton#ghost { background: #3a3a3a; }
QPushButton#ghost:hover { background: #4a4a4a; }
QPushButton#go { background: #2d8a4e; font-size: 12.5pt; font-weight: 700; padding: 11px 30px; }
QPushButton#go:hover { background: #36a35d; }
QPushButton#go:disabled { background: #2a4a35; color: #8aa; }
QPushButton#merge { background: #16a085; font-weight: 700; }
QPushButton#merge:hover { background: #1abc9c; }
QPushButton#merge:disabled { background: #1d4a43; color: #8aa; }
QPushButton#stop { background: #a83232; }
QPushButton#stop:hover { background: #c0392b; }
QPushButton#stop:disabled { background: #3a3a3a; color: #777; }
QCheckBox, QRadioButton { spacing: 7px; padding: 2px 0; }
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled { color: #6f6f6f; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; border: 2px solid #8a8a8a; background: #262626; }
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #3b8ed0; }
QCheckBox::indicator:checked { background: #1f6aa5; border-color: #1f6aa5; image: url(TICK_ICON); }
QRadioButton::indicator:checked { width: 8px; height: 8px; border: 5px solid #1f6aa5; background: #ffffff; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { border-color: #4a4a4a; }
QCheckBox::indicator:checked:disabled { background: #3d5366; border-color: #3d5366; }
QRadioButton#blue { color: #3b9ee0; }
QRadioButton#red { color: #e74c3c; }
QCheckBox#accent { color: #f1c40f; }
QProgressBar { background: #2f2f2f; border: none; border-radius: 4px; height: 9px; }
QProgressBar::chunk { background: #1f8fe0; border-radius: 4px; }
"""


def apply_theme(app):
    """Fusion + a dark palette so check marks, radio dots and spin arrows draw natively."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.Window, "#1e1e1e"), (QPalette.WindowText, "#e6e6e6"),
                        (QPalette.Base, "#2f2f2f"), (QPalette.AlternateBase, "#262626"),
                        (QPalette.Text, "#e6e6e6"), (QPalette.Button, "#3a3a3a"),
                        (QPalette.ButtonText, "#e6e6e6"), (QPalette.ToolTipBase, "#2a2a2a"),
                        (QPalette.ToolTipText, "#e6e6e6"), (QPalette.PlaceholderText, "#8a8a8a"),
                        (QPalette.Highlight, "#1f6aa5"), (QPalette.HighlightedText, "#ffffff")):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#6f6f6f"))
    app.setPalette(palette)
    # Qt style sheets can only take the check mark from an image file, so draw one.
    tick = QPixmap(28, 28)
    tick.fill(Qt.transparent)
    painter = QPainter(tick)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("white"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPolyline([QPointF(6, 15), QPointF(12, 21), QPointF(22, 8)])
    painter.end()
    tick_path = os.path.join(tempfile.gettempdir(), "file_converter_tick.png")
    tick.save(tick_path)
    app.setStyleSheet(STYLE.replace("TICK_ICON", tick_path.replace("\\", "/")))


class NoScrollSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class Worker(QThread):
    """Runs an engine job off the UI thread; the engine fans out its own thread pools."""
    log = Signal(str)
    progress = Signal(int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.cancel = threading.Event()
        self._last = -1

    def _progress(self, fraction):
        value = int(fraction * 1000)
        if value != self._last:  # worker threads report far more often than the bar can show
            self._last = value
            self.progress.emit(value)

    def run(self):
        try:
            self.finished_ok.emit(self.job(self.log.emit, self._progress, self.cancel))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class FileConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setAcceptDrops(True)
        self.current_settings = self.load_settings()
        self.files: list[Path] = []
        self.worker = None

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.resize(min(1080, int(geo.width() * 0.9)), min(940, int(geo.height() * 0.9)))
        else:
            self.resize(1000, 860)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 14)
        root.setSpacing(10)

        title = QLabel("File Converter & Chapter Splitter", objectName="title")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # --- source files -------------------------------------------------
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.select_button = QPushButton("Select Source Files (TXT, PDF, EPUB)")
        self.select_button.clicked.connect(self.on_select_files)
        self.folder_button = QPushButton("Add Folder", objectName="ghost")
        self.folder_button.clicked.connect(self.on_select_folder)
        self.clear_button = QPushButton("Clear", objectName="ghost")
        self.clear_button.clicked.connect(self.clear_files)
        for b in (self.select_button, self.folder_button, self.clear_button):
            buttons.addWidget(b)
        buttons.addStretch()
        root.addLayout(buttons)

        self.file_label = QLabel(objectName="hint")
        self.file_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.file_label)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setFixedHeight(92)
        self.file_list.setVisible(False)
        QShortcut(QKeySequence.Delete, self.file_list, activated=self.remove_selected)
        root.addWidget(self.file_list)

        # --- options ------------------------------------------------------
        s = self.current_settings
        panel = QFrame(objectName="panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(26)

        def column(col, heading):
            box = QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(QLabel(heading, objectName="heading"))
            grid.addLayout(box, 0, col, Qt.AlignTop)
            return box

        def radios(box, key, options, default):
            group = QButtonGroup(self)
            chosen = s.get(key, default)
            for value, text, name in options:
                rb = QRadioButton(text, objectName=name)
                rb.setProperty("value", value)
                rb.setChecked(value == chosen)
                group.addButton(rb)
                box.addWidget(rb)
            if group.checkedButton() is None:
                group.buttons()[0].setChecked(True)
            group.buttonClicked.connect(lambda _: self.on_option_changed())
            return group

        box = column(0, "Output Formats:")
        self.format_checks = {}
        for key, text in (("txt", "TXT"), ("pdf", "PDF"), ("epub", "EPUB"), ("csv", "CSV (Master)")):
            cb = QCheckBox(text)
            cb.setChecked(key in s.get("formats", ["pdf"]))
            cb.stateChanged.connect(self.on_option_changed)
            self.format_checks[key] = cb
            box.addWidget(cb)
        box.addStretch()

        box = column(1, "File Mode:")
        self.file_mode = radios(box, "file_mode", [("merged", "One Merged File", ""),
                                                   ("separate", "Separate Chapters", "")], "merged")
        self.subfolders_check = QCheckBox("Subfolder per Input File")
        self.subfolders_check.setChecked(s.get("subfolders", True))
        self.subfolders_check.setToolTip(
            "All output goes into one \"Converted\" folder next to your files.\n"
            "On: each input gets its own subfolder inside it (Converted\\<name>).\n"
            "Off: every output file goes straight into Converted.")
        self.subfolders_check.stateChanged.connect(self.on_option_changed)
        box.addSpacing(4)
        box.addWidget(self.subfolders_check)
        box.addStretch()

        box = column(2, "Spacing:")
        self.spacing = radios(box, "spacing", [("standard", "Standard", ""),
                                               ("double", "Double Space", ""),
                                               ("remove", "Remove Blanks", "")], "standard")
        box.addStretch()

        box = column(3, "Chapter Format Mode:")
        self.chapter_mode = radios(box, "chapter_mode", [
            ("auto", "Smart Auto (Paste Example)", "blue"),
            ("none", "No Splitting (Convert Only)", ""),
            ("numeric", "00 Prologue / 1 / 01 ...", ""),
            ("korean", "1화. / 2화. (Korean)", ""),
            ("hash", "#001. / #002.", ""),
            ("naver", "Via Naver Series Link", "red")], "auto")
        self.split_notes_check = QCheckBox("Split Author's Notes Into Own Chapters")
        self.split_notes_check.setChecked(s.get("split_notes", False))
        self.split_notes_check.setToolTip(
            "Off: notices such as 작가의 말 / 후기 / Author's Note stay at the end of the chapter\n"
            "they follow. On: each one becomes a separate chapter.")
        self.split_notes_check.stateChanged.connect(self.on_option_changed)
        box.addSpacing(4)
        box.addWidget(self.split_notes_check)
        box.addStretch()

        box = column(4, "PDF Settings:")
        self.page_numbers_check = QCheckBox("Add Page Numbers to Footer")
        self.page_numbers_check.setChecked(s.get("page_numbers", True))
        self.toc_check = QCheckBox("Generate Table of Contents")
        self.toc_check.setChecked(s.get("toc", False))
        self.toc_numbers_check = QCheckBox("Add Page Numbers to TOC")
        self.toc_numbers_check.setChecked(s.get("toc_numbers", True))
        self.keep_layout_check = QCheckBox("Keep EPUB Images && Styling")
        self.keep_layout_check.setChecked(s.get("keep_layout", True))
        self.keep_layout_check.setToolTip(
            "EPUB → merged PDF is rendered with WeasyPrint so images and the book's CSS survive.\n"
            "Needs the GTK runtime (MSYS2); falls back to a text PDF when unavailable.")
        for cb in (self.page_numbers_check, self.toc_check, self.toc_numbers_check,
                   self.keep_layout_check):
            cb.stateChanged.connect(self.on_option_changed)
            box.addWidget(cb)
        start_row = QHBoxLayout()
        self.toc_start_label = QLabel("Start Page Number:")
        self.toc_start_page_spin = NoScrollSpinBox()
        self.toc_start_page_spin.setFixedWidth(80)
        self.toc_start_page_spin.setRange(1, 9999)
        self.toc_start_page_spin.setValue(s.get("toc_start_page", 1))
        self.toc_start_page_spin.valueChanged.connect(self.on_option_changed)
        start_row.addWidget(self.toc_start_label)
        start_row.addWidget(self.toc_start_page_spin)
        start_row.addStretch()
        box.addLayout(start_row)
        box.addStretch()
        grid.setColumnStretch(5, 1)
        root.addWidget(panel)

        # --- log + example panel -----------------------------------------
        middle = QHBoxLayout()
        middle.setSpacing(12)
        log_box = QVBoxLayout()
        log_box.setSpacing(6)
        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setPlainText(f"Smart Engine v{APP_VERSION} Ready.\n"
                                     "Drop TXT / PDF / EPUB files or folders anywhere on this window.")
        if dll_guard.pinned:
            self.log("Ignored stray DLLs next to the program (using the genuine ones instead): "
                     + ", ".join(name for name, _ in dll_guard.pinned))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        log_box.addWidget(self.log_viewer)
        log_box.addWidget(self.progress_bar)
        middle.addLayout(log_box, 1)

        side = QFrame(objectName="side")
        side.setFixedWidth(290)
        side_layout = QVBoxLayout(side)
        self.side_stack = QStackedWidget()
        side_layout.addWidget(self.side_stack)

        def side_page(label, label_name, placeholder, hint, key, multiline=False):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setSpacing(10)
            head = QLabel(label, objectName=label_name)
            head.setAlignment(Qt.AlignCenter)
            if multiline:
                entry = QPlainTextEdit(s.get(key, ""))
                entry.setFixedHeight(118)
                entry.textChanged.connect(self.save_settings)
                entry.text = entry.toPlainText  # same accessor as the single-line boxes
            else:
                entry = QLineEdit(s.get(key, ""))
                entry.editingFinished.connect(self.save_settings)
            entry.setPlaceholderText(placeholder)
            note = QLabel(hint, objectName="hint")
            note.setAlignment(Qt.AlignCenter)
            note.setWordWrap(True)
            lay.addWidget(head)
            lay.addWidget(entry)
            lay.addWidget(note)
            lay.addStretch()
            self.side_stack.addWidget(page)
            return entry

        self.example_entry = side_page(
            "Paste Examples Here:", "accent", "e.g.\n2화. 내가 허락했어\n사이버펑크 협객전 127화",
            "Paste how chapter headings look in your file, one example per line — "
            "add one for each style the file uses.\n\n"
            "Leave it empty to auto-detect. Either way, headings with a title prefix, "
            "two-line headings and title-only chapters are picked up automatically.",
            "example", multiline=True)
        self.naver_entry = side_page(
            "Naver Series URL:", "naver", "https://series.naver.com/novel/detail.series?productNo=…",
            "Fetches the official episode list and splits on those titles. "
            "Irregular tags such as (삽화) or [수정] are cleaned automatically.", "naver_url")
        info = QWidget()
        info_layout = QVBoxLayout(info)
        self.side_info = QLabel(objectName="hint")
        self.side_info.setAlignment(Qt.AlignCenter)
        self.side_info.setWordWrap(True)
        info_layout.addWidget(self.side_info)
        info_layout.addStretch()
        self.side_stack.addWidget(info)
        middle.addWidget(side)
        root.addLayout(middle, 1)

        # --- prefix / threads --------------------------------------------
        prefix_row = QHBoxLayout()
        prefix_row.addStretch()
        self.prefix_check = QCheckBox("Format Chapter Prefix/Suffix:", objectName="accent")
        self.prefix_check.setChecked(s.get("use_prefix", False))
        self.prefix_check.stateChanged.connect(self.on_option_changed)
        self.prefix_entry = QLineEdit(s.get("prefix", "Ch.{n}"))
        self.prefix_entry.setFixedWidth(90)
        self.prefix_entry.editingFinished.connect(self.save_settings)
        prefix_row.addWidget(self.prefix_check)
        prefix_row.addWidget(self.prefix_entry)
        prefix_row.addWidget(QLabel("(Use {n} for number, e.g. Ch.{n} or {n}화)", objectName="hint"))
        prefix_row.addSpacing(20)
        self.number_titles_check = QCheckBox("Number Titles, start at", objectName="accent")
        self.number_titles_check.setChecked(s.get("number_titles", False))
        self.number_titles_check.setToolTip(
            "Put each chapter's position in front of its title, e.g. \"1. Prologue\", \"2. 1화 …\".\n"
            "Start at 0 to number a prologue as \"0.\".")
        self.number_titles_check.stateChanged.connect(self.on_option_changed)
        self.number_start_spin = NoScrollSpinBox()
        self.number_start_spin.setRange(0, 99999)
        self.number_start_spin.setFixedWidth(70)
        self.number_start_spin.setValue(s.get("number_start", 1))
        self.number_start_spin.setToolTip("First number used: 0 → \"0. \", \"1. \" …   1 → \"1. \", \"2. \" …")
        self.number_start_spin.valueChanged.connect(self.on_option_changed)
        prefix_row.addWidget(self.number_titles_check)
        prefix_row.addWidget(self.number_start_spin)
        prefix_row.addSpacing(20)
        prefix_row.addWidget(QLabel("Parallel threads:"))
        self.threads_spin = NoScrollSpinBox()
        self.threads_spin.setRange(1, engine.LOGICAL_CORES)  # out-of-range saved values clamp
        self.threads_spin.setFixedWidth(70)
        self.threads_spin.setValue(s.get("threads", engine.DEFAULT_WORKERS))
        self.threads_spin.valueChanged.connect(self.on_option_changed)
        cores = f"{engine.LOGICAL_CORES} logical core{'s' if engine.LOGICAL_CORES != 1 else ''}"
        self.threads_spin.setToolTip(f"1 – {engine.LOGICAL_CORES} (this PC has {cores})")
        prefix_row.addWidget(self.threads_spin)
        prefix_row.addWidget(QLabel(f"/ {cores}", objectName="hint"))
        prefix_row.addStretch()
        root.addLayout(prefix_row)

        # --- actions ------------------------------------------------------
        go_row = QHBoxLayout()
        go_row.addStretch()
        self.convert_button = QPushButton("CONVERT / PROCESS", objectName="go")
        self.convert_button.clicked.connect(self.on_convert)
        self.cancel_button = QPushButton("Cancel", objectName="stop")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.on_cancel)
        go_row.addWidget(self.convert_button)
        go_row.addWidget(self.cancel_button)
        go_row.addStretch()
        root.addLayout(go_row)

        merge_row = QHBoxLayout()
        merge_row.addStretch()
        self.merge_button = QPushButton("MERGE FILES (TXT/PDF/EPUB) TO 1 FILE (TXT/PDF/EPUB)",
                                        objectName="merge")
        self.merge_button.clicked.connect(self.on_merge)
        merge_row.addWidget(self.merge_button)
        merge_row.addStretch()
        root.addLayout(merge_row)

        self.refresh_files()
        self.update_option_states()

    # ---- settings ----------------------------------------------------------

    def load_settings(self):
        try:
            if SETTINGS_FILE.exists():
                settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if settings.get("settings_version", 1) < SETTINGS_VERSION:
                    # Early builds saved their old default of 16 threads; use the
                    # current default instead of treating that as a user choice.
                    settings.pop("threads", None)
                return settings
        except Exception:
            pass
        return {}

    def save_settings(self):
        settings = {
            "settings_version": SETTINGS_VERSION,
            "formats": [k for k, cb in self.format_checks.items() if cb.isChecked()],
            "file_mode": self._value(self.file_mode),
            "spacing": self._value(self.spacing),
            "chapter_mode": self._value(self.chapter_mode),
            "example": self.example_entry.text(),
            "naver_url": self.naver_entry.text(),
            "use_prefix": self.prefix_check.isChecked(),
            "prefix": self.prefix_entry.text(),
            "threads": self.threads_spin.value(),
            "page_numbers": self.page_numbers_check.isChecked(),
            "toc": self.toc_check.isChecked(),
            "toc_numbers": self.toc_numbers_check.isChecked(),
            "toc_start_page": self.toc_start_page_spin.value(),
            "keep_layout": self.keep_layout_check.isChecked(),
            "split_notes": self.split_notes_check.isChecked(),
            "number_titles": self.number_titles_check.isChecked(),
            "number_start": self.number_start_spin.value(),
            "subfolders": self.subfolders_check.isChecked(),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        except OSError as e:
            self.log(f"Could not save settings: {e}")

    @staticmethod
    def _value(group):
        return group.checkedButton().property("value")

    def on_option_changed(self, *_):
        self.update_option_states()
        self.save_settings()

    def update_option_states(self):
        mode = self._value(self.chapter_mode)
        if mode == "auto":
            self.side_stack.setCurrentIndex(0)
        elif mode == "naver":
            self.side_stack.setCurrentIndex(1)
        else:
            self.side_info.setText({
                "none": "Files are converted as they are.\n\nEPUBs keep their own chapters; "
                        "TXT and PDF become a single section.",
                "numeric": "Splits on lines that are a bare number, optionally followed by a "
                           "title:\n\n00 Prologue\n1\n01 Title\n\nOnly numbers that rise in "
                           "sequence count, so stray numbers in the text are ignored.",
                "korean": "Splits on lines such as:\n\n1화\n2화. 제목\n제3화",
                "hash": "Splits on lines such as:\n\n#001.\n#002. Title",
            }[mode])
            self.side_stack.setCurrentIndex(2)
        pdf = self.format_checks["pdf"].isChecked()
        toc = pdf and self.toc_check.isChecked()
        self.page_numbers_check.setEnabled(pdf)
        self.toc_check.setEnabled(pdf)
        self.keep_layout_check.setEnabled(pdf)
        self.toc_numbers_check.setEnabled(toc)
        self.toc_start_label.setEnabled(pdf)
        self.toc_start_page_spin.setEnabled(pdf)
        self.prefix_entry.setEnabled(self.prefix_check.isChecked())
        self.number_start_spin.setEnabled(self.number_titles_check.isChecked())

    def build_settings(self):
        return engine.Settings(
            formats={k for k, cb in self.format_checks.items() if cb.isChecked()},
            merged=self._value(self.file_mode) == "merged",
            spacing=self._value(self.spacing),
            mode=self._value(self.chapter_mode),
            example=self.example_entry.text(),
            naver_url=self.naver_entry.text().strip(),
            template=(self.prefix_entry.text().strip() or None) if self.prefix_check.isChecked() else None,
            workers=self.threads_spin.value(),
            page_numbers=self.page_numbers_check.isChecked(),
            toc=self.toc_check.isChecked(),
            toc_numbers=self.toc_numbers_check.isChecked(),
            start_page=self.toc_start_page_spin.value(),
            keep_layout=self.keep_layout_check.isChecked(),
            split_notes=self.split_notes_check.isChecked(),
            number_titles=self.number_titles_check.isChecked(),
            number_start=self.number_start_spin.value(),
            subfolders=self.subfolders_check.isChecked(),
        )

    # ---- file selection ----------------------------------------------------

    def add_paths(self, paths):
        known = {str(p.resolve()).lower() for p in self.files}
        added = 0
        for f in engine.collect_files(paths):
            key = str(f.resolve()).lower()
            if key not in known:
                known.add(key)
                self.files.append(f)
                added += 1
        if added:
            self.files.sort(key=engine.natural_key)
            self.refresh_files()
            self.log(f"Added {added} file(s); {len(self.files)} queued.")
        elif paths:
            self.log("No new TXT / PDF / EPUB files found in the selection.")

    def refresh_files(self):
        self.file_list.clear()
        self.file_list.addItems([f"{p.name}     —  {p.parent}" for p in self.files])
        self.file_list.setVisible(bool(self.files))
        if not self.files:
            self.file_label.setText("No file selected — click the button or drag & drop files / folders here")
        elif len(self.files) == 1:
            self.file_label.setText(f"Selected: {self.files[0].name}")
        else:
            self.file_label.setText(f"{len(self.files)} files selected (batch)   ·   "
                                    "Delete removes the highlighted ones")

    def on_select_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Source Files", "", FILE_FILTER)
        if paths:
            self.add_paths(paths)

    def on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder (all TXT / PDF / EPUB inside are added)")
        if folder:
            self.add_paths([folder])

    def clear_files(self):
        self.files = []
        self.refresh_files()

    def remove_selected(self):
        rows = {i.row() for i in self.file_list.selectedIndexes()}
        if rows:
            self.files = [f for i, f in enumerate(self.files) if i not in rows]
            self.refresh_files()

    def dragEnterEvent(self, event):
        if self.worker is None and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    # ---- running jobs ------------------------------------------------------

    def log(self, message):
        self.log_viewer.append(f"> {message}")

    def set_busy(self, busy):
        for w in (self.select_button, self.folder_button, self.clear_button,
                  self.convert_button, self.merge_button):
            w.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.convert_button.setText("PROCESSING…" if busy else "CONVERT / PROCESS")

    def start(self, job, on_done):
        self.progress_bar.setValue(0)
        self.set_busy(True)
        self.worker = Worker(job, self)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished_ok.connect(on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_thread_finished)
        self.worker.start()

    def on_thread_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_busy(False)

    def on_failed(self, message):
        self.log(f"ERROR: {message}")
        QMessageBox.critical(self, "Error", f"Operation failed:\n{message}")

    def on_cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.log("Cancelling… (files already being written will finish)")

    def on_convert(self):
        self.save_settings()
        settings = self.build_settings()
        if not self.files:
            QMessageBox.warning(self, "No files", "Select or drop at least one TXT, PDF or EPUB file.")
            return
        if not settings.formats:
            QMessageBox.warning(self, "No output format", "Tick at least one output format.")
            return
        if settings.mode == "naver" and "productNo=" not in settings.naver_url:
            QMessageBox.warning(self, "Naver Series link",
                                "Paste a Naver Series link that contains productNo=… first.")
            return
        files = list(self.files)
        self.start(lambda log, progress, cancel: engine.process_batch(files, settings, log,
                                                                      progress, cancel),
                   self.on_convert_done)

    def on_convert_done(self, results):
        ok = [r for r in results if "error" not in r]
        failed = [r for r in results if "error" in r and r["error"] != "cancelled"]
        cancelled = len(results) - len(ok) - len(failed)
        self.progress_bar.setValue(1000)
        summary = self.summarize(ok, failed, cancelled)
        roots = list(dict.fromkeys(str(r["root"]) for r in ok))  # one per source folder
        self.log(" | ".join(line.strip() for line in summary.splitlines() if line.strip()))
        box = QMessageBox(QMessageBox.Warning if failed else QMessageBox.Information,
                          "Done", summary, QMessageBox.Ok, self)
        label = "Open Output Folder" if len(roots) <= 1 else "Open Output Folders"
        open_button = box.addButton(label, QMessageBox.ActionRole) if ok else None
        box.exec()
        if open_button is not None and box.clickedButton() is open_button:
            for root in roots:  # usually one; one per source folder if inputs came from several
                QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    @staticmethod
    def summarize(ok, failed, cancelled, max_listed=8):
        """Done-dialog text: what went in (input files) and what came out (per format)."""
        plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
        # Results arrive in finishing order; list them the way the file list shows them.
        ok = sorted(ok, key=lambda r: engine.natural_key(r["file"]))
        failed = sorted(failed, key=lambda r: engine.natural_key(r["file"]))
        lines = [f"Input files: {len(ok)} converted"
                 + (f", {len(failed)} failed" if failed else "")
                 + (f", {cancelled} cancelled" if cancelled else "")]
        for r in ok[:max_listed]:
            lines.append(f"    • {r['file'].name}  —  {plural(r['chapters'], 'chapter')}")
        if len(ok) > max_listed:
            lines.append(f"    • … and {len(ok) - max_listed} more")
        for r in failed[:max_listed]:
            lines.append(f"    ✗ {Path(r['file']).name}  —  failed (see the log)")

        by_format: dict[str, int] = {}
        for r in ok:
            for fmt, n in r.get("by_format", {}).items():
                by_format[fmt] = by_format.get(fmt, 0) + n
        total = sum(by_format.values())
        lines += ["", f"Output files: {total} written"]
        for fmt in ("txt", "pdf", "epub", "csv"):
            if fmt in by_format:
                lines.append(f"    • {fmt.upper()}: {plural(by_format[fmt], 'file')}")

        roots = list(dict.fromkeys(str(r["root"]) for r in ok))
        if roots:
            lines += ["", "Output folder:" if len(roots) == 1 else "Output folders:"] + roots
        return "\n".join(lines)

    def on_merge(self):
        self.save_settings()
        files = list(self.files)
        if len(files) < 2:
            paths, _ = QFileDialog.getOpenFileNames(self, "Select the files to merge (2 or more)",
                                                    "", FILE_FILTER)
            files = sorted(engine.collect_files(paths), key=engine.natural_key)
            if len(files) < 2:
                if paths:
                    QMessageBox.warning(self, "Merge", "Pick at least two files to merge.")
                return
        default = files[0].parent / f"{files[0].parent.name or 'Merged'}_Merged.txt"
        out, _ = QFileDialog.getSaveFileName(
            self, f"Merge {len(files)} files into…", str(default),
            "Text file (*.txt);;PDF document (*.pdf);;EPUB e-book (*.epub)")
        if not out:
            return
        if Path(out).suffix.lower() not in (".txt", ".pdf", ".epub"):
            out += ".txt"
        settings = self.build_settings()
        self.start(lambda log, progress, cancel: (engine.merge_files(files, Path(out), settings,
                                                                     log, progress), out),
                   self.on_merge_done)

    def on_merge_done(self, result):
        count, out = result
        QMessageBox.information(self, "Merged", f"Merged {count} sections into:\n{out}")

    def closeEvent(self, event):
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait(5000)
        self.save_settings()
        event.accept()


def run_cli(argv):
    """Headless batch mode: converter --cli [options] files/folders…"""
    import argparse
    for stream in (sys.stdout, sys.stderr):  # e.g. "→" or Korean titles on a cp1252 console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="converter --cli")
    parser.add_argument("paths", nargs="+", help="TXT / PDF / EPUB files or folders")
    parser.add_argument("--formats", default="pdf", help="comma list of txt,pdf,epub,csv")
    parser.add_argument("--separate", action="store_true", help="one file per chapter")
    parser.add_argument("--spacing", default="standard", choices=["standard", "double", "remove"])
    parser.add_argument("--mode", default="auto", choices=["auto", "none", "numeric", "korean", "hash"])
    parser.add_argument("--example", default="", help="example chapter heading for auto mode")
    parser.add_argument("--prefix", default=None, help="chapter title template, e.g. Ch.{n}")
    parser.add_argument("--toc", action="store_true", help="add a table of contents to PDFs")
    parser.add_argument("--keep-layout", action="store_true",
                        help="EPUB -> PDF through WeasyPrint (images + CSS)")
    parser.add_argument("--split-notes", action="store_true",
                        help="make author's notes (e.g. Author's Note) separate chapters")
    parser.add_argument("--number-start", type=int, default=1,
                        help="first number for --number-titles (e.g. 0 or 1; default 1)")
    parser.add_argument("--no-subfolders", action="store_true",
                        help="put all output straight into the Converted folder (no per-input subfolders)")
    parser.add_argument("--number-titles", action="store_true",
                        help='put each chapter\'s position in front of its title ("1. ", "2. " ...)')
    parser.add_argument("--threads", type=engine.clamp_workers, default=engine.DEFAULT_WORKERS,
                        help=f"1-{engine.LOGICAL_CORES} (logical cores on this PC)")
    parser.add_argument("--log", default=None, help="also write the log to this file")
    args = parser.parse_args(argv)
    lines = []

    def log(message):
        lines.append(message)
        try:
            print(message, flush=True)
        except (OSError, UnicodeError):
            pass

    settings = engine.Settings(
        formats=set(args.formats.lower().split(",")), merged=not args.separate,
        spacing=args.spacing, mode=args.mode, example=args.example, template=args.prefix,
        toc=args.toc, keep_layout=args.keep_layout, workers=args.threads,
        split_notes=args.split_notes, number_titles=args.number_titles,
        number_start=args.number_start,
        subfolders=not args.no_subfolders)
    results = engine.process_batch(engine.collect_files(args.paths), settings, log, lambda f: None)
    if args.log:
        Path(args.log).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if any("error" in r for r in results) else 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        sys.exit(run_cli(sys.argv[2:]))
    app = QApplication(sys.argv)
    apply_theme(app)
    window = FileConverter()
    window.show()
    sys.exit(app.exec())
