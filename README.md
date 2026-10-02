# File Converter & Chapter Splitter

Converts between **TXT, PDF and EPUB** in any direction, splits novels into chapters, and merges many files into one.

## Features

- **Any-to-any conversion** – TXT / PDF / EPUB in, TXT / PDF / EPUB / CSV (master sheet) out. Tick several output formats to get them all in one run.
- **Batch + drag & drop** – select many files, add a folder, or drop files/folders anywhere on the window. Files are processed in parallel (thread count is adjustable).
- **File mode** – one merged file per source, or one file per chapter.
- **Chapter detection**
  - *Smart Auto* – paste one heading exactly as it appears (`Chapter 12: Title`, `2화. 제목`, `第十二章`, `[외전]` …) or leave it empty to auto-detect.
  - *No Splitting* – plain conversion; EPUBs keep their own chapters.
  - `00 Prologue / 1 / 01`, `1화. / 2화.` and `#001. / #002.` presets.
  - *Naver Series link* – fetches the official episode list and splits on those titles.
- **Spacing** – standard, double space, or remove blank lines.
- **Chapter prefix/suffix** – rename headings with a template such as `Ch.{n}` or `{n}화`.
- **PDF settings** – footer page numbers, table of contents (with or without page numbers), start page number, and layout-preserving EPUB → PDF (images + CSS, via WeasyPrint).
- **Merge** – combine many TXT / PDF / EPUB files into a single TXT, PDF or EPUB.

Output goes to a `<name>_Converted` folder next to each source file.

## Run

```
pip install -r requirements.txt
run.bat
```

Headless batch mode: `python converter.py --cli --formats txt,epub book.pdf folder\` (see `--cli -h`).

## Build

`build.bat` produces `dist\converter.exe` from `converter.spec`.

The layout-preserving EPUB → PDF option needs the GTK runtime (MSYS2 `mingw64`); without it the app falls back to its built-in text PDF writer.
