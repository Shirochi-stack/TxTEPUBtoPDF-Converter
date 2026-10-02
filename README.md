# File Converter & Chapter Splitter

Converts between **TXT, PDF and EPUB** in any direction, splits novels into chapters, and merges many files into one.

## Features

- **Any-to-any conversion** – TXT / PDF / EPUB in, TXT / PDF / EPUB / CSV (master sheet) out. Tick several output formats to get them all in one run.
- **Batch + drag & drop** – select many files, add a folder, or drop files/folders anywhere on the window. Files are processed in parallel (thread count is adjustable).
- **File mode** – one merged file per source, or one file per chapter.
- **Chapter detection** – see [Chapter detection](#chapter-detection) below.
- **Spacing** – standard, double space, or remove blank lines.
- **Chapter prefix/suffix** – rename headings with a template such as `Ch.{n}` or `{n}화`.
- **PDF settings** – footer page numbers, table of contents (with or without page numbers), start page number, and layout-preserving EPUB → PDF (images + CSS, via WeasyPrint).
- **Merge** – combine many TXT / PDF / EPUB files into a single TXT, PDF or EPUB.

Output goes to a `<name>_Converted` folder next to each source file.

## Chapter detection

Pick a **Chapter Format Mode**:

| Mode | What it splits on |
|---|---|
| **Smart Auto** | Headings like the examples you paste, one per line (`Chapter 12: Title`, `2화. 제목`, `第十二章`, `[외전]` …). Leave the box empty to auto-detect common styles. |
| **No Splitting** | Nothing – a plain conversion. EPUBs keep their own chapters. |
| **00 Prologue / 1 / 01** | Lines that are a bare number, optionally followed by a title. Only numbers that rise in sequence count. |
| **1화. / 2화.** | Korean `N화` headings (`1화`, `2화. 제목`, `제3화`). |
| **#001. / #002.** | Hash-numbered headings. |
| **Naver Series link** | The official episode titles fetched from a `series.naver.com` link (`productNo=…`). Tags such as (삽화) or [수정] are cleaned up. |

### Books that change heading style

Web novels often change how chapter headings look partway through. In all modes except No Splitting and Naver Series, the app handles that without extra setup:

- **Title prefixes** – a work title repeated before the number is recognised: `사이버펑크 협객전 127화`.
- **Two-line headings** – a heading line with only the number is joined with the title line beneath it:

  ```
  사이버펑크 협객전 127화

  EP5. 화룡점정(畵龍點睛) 17
  ```

  becomes one chapter titled `사이버펑크 협객전 127화 EP5. 화룡점정(畵龍點睛) 17`.
- **Title-only chapters** – where numbered headings stop, chapters headed only by a title are found and numbered in sequence:
  - title series such as `EP.6 별이 빛나는 밤에`, `EP.6 별이 빛나는 밤에 2`, `EP.6 별이 빛나는 밤에 3`
  - single titles such as `계약` or `LET'S RIDE!`, picked when they sit about one chapter's length apart
- **Missing chapters** – where a number is skipped (137 → 139), the app looks for an unlabelled chapter in between. If there is none, the log names the chapter that is not in the file instead of renumbering everything after it.
- **Noise is ignored** – scene breaks (`* * *`), inserted blocks (`//* … *//`), comment threads, sound effects and table-of-contents pages are not treated as chapter headings.

A book whose chapters are numbered 1, 2, 3… with no gaps is never split further, even when some chapters are long.

In **Separate Chapters** mode, files are named by chapter number (`137 …`, `139 …`). A chapter missing from the source therefore doesn't shift the names of the files after it.

## Run

```
pip install -r requirements.txt
run.bat
```

Headless batch mode: `python converter.py --cli --formats txt,epub book.pdf folder\` (see `--cli -h`).

## Build

`build.bat` produces `dist\TxTEPUBtoPDF-Converter.v<version>.exe` (e.g. `TxTEPUBtoPDF-Converter.v2.1.exe`) from `converter.spec`; the version comes from `APP_VERSION` in `converter.py`, and the publisher details (shirochi-stack) from `version_info.txt`.

The layout-preserving EPUB → PDF option needs the GTK runtime (MSYS2 `mingw64`); without it the app falls back to its built-in text PDF writer.
