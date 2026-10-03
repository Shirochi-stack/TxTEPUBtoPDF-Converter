# File Converter & Chapter Splitter

Converts between **TXT, PDF and EPUB** in any direction, splits novels into chapters, and merges many files into one.

## Download

Get `TxTEPUBtoPDF-Converter.v<version>.exe` from the [latest release](https://github.com/Shirochi-stack/TxTEPUBtoPDF-Converter/releases/latest). No install needed; your settings are saved to `config.json` next to the exe.

## Features

- **Any-to-any conversion** – TXT / PDF / EPUB in, TXT / PDF / EPUB / CSV (master sheet) out. Tick several output formats to get them all in one run.
- **Batch + drag & drop** – select many files, add a folder, or drop files/folders anywhere on the window.
- **Parallel threads** – files are processed in parallel. The default is 2 threads; you can raise it up to your PC's logical core count, which is shown next to the setting (e.g. `/ 16 logical cores`).
- **File mode** – one merged file per source, or one file per chapter (optionally with numbered file names).
- **Chapter detection** – see [Chapter detection](#chapter-detection) below.
- **Spacing** – standard, double space, or remove blank lines.
- **Chapter prefix/suffix** – rename headings with a template such as `Ch.{n}` or `{n}화`.
- **Number Titles** – optionally put each chapter's position in front of its title. Choose the first number in the **start at** box:
  - `1` (default) gives `1. Prologue`, `2. 1화 …`
  - `0` gives `0. Prologue`, `1. 1화 …`

  Front matter is left unnumbered, and merging already-numbered files doesn't stack numbers.
- **PDF settings** – footer page numbers, table of contents (with or without page numbers), start page number, and layout-preserving EPUB → PDF (images + CSS, via WeasyPrint).
- **Merge** – combine many TXT / PDF / EPUB files into a single TXT, PDF or EPUB, saved wherever you choose.

## Output

All output goes into one main **`Converted`** folder next to your source files. Inside it, each input gets its own subfolder by default:

```
Novels\
  alpha.txt
  book.epub
  Converted\
    alpha\   alpha.pdf, alpha.epub …
    book\    book.pdf, book.epub …
```

- **Subfolder per Input File** (on by default) controls the folders inside `Converted`. Untick it (or use `--no-subfolders`) to put every output file straight into `Converted\`. Separate-chapter files then go into per-format folders (`Converted\TXT\`, `Converted\PDF\` …) with the source name in front of each file name.
- Inputs in one folder that share a name (`book.txt` + `book.epub`) get their extension added (`book_txt`, `book_epub`) so their outputs can't overwrite each other.
- When you add a folder, anything already inside its `Converted` folder is skipped, so earlier results aren't converted again.
- Source files are never modified.

### When a batch finishes

The **Done** dialog lists what went in separately from what came out:

```
Input files: 3 converted, 1 failed
    • alpha.txt  —  13 chapters
    • book.epub  —  291 chapters
    • notes.pdf  —  4 chapters
    ✗ empty.txt  —  failed (see the log)

Output files: 924 written
    • TXT: 308 files
    • PDF: 308 files
    • EPUB: 308 files

Output folder:
C:\…\Novels\Converted
```

- **Input files** are listed in the same order as your file list, with their chapter counts. Failed or cancelled files are marked; a long batch ends with "… and N more".
- **Output files** shows the total and a count per format. Only files that were actually written are counted.
- **Open Output Folder** opens the main `Converted` folder. If the inputs came from several folders, each one has its own `Converted` folder and the button opens all of them.

The log gets the same summary on one line.

## Chapter detection

Pick a **Chapter Format Mode**:

| Mode | What it splits on |
|---|---|
| **Smart Auto** | Headings like the examples you paste, one per line (`Chapter 12: Title`, `2화. 제목`, `第十二章`, `[외전]` …). Leave the box empty to auto-detect common styles. |
| **No Splitting** | Nothing – a plain conversion. EPUBs keep their own chapters. |
| **00 Prologue / 1 / 01** | Lines that are a bare number, optionally followed by a title. Only numbers that rise in sequence count. |
| **1화. / 2화.** | Korean `N화` headings (`1화`, `#1화 제목`, `2화. 제목`, `제3화`). |
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
- **Marker + title headings** – a marker line directly followed by the chapter's title line is one heading:

  ```
  < 천마를 삼켰다 외전 1화 >

  1화 신들의 전쟁
  ```

  becomes `천마를 삼켰다 외전 1화 신들의 전쟁`.
- **Side stories keep their own numbers** – chapters marked `외전`, `번외` or `Side Story` are numbered separately (외전 1, 2 …), so they don't restart or shift the main chapter numbers. With a `Ch.{n}` template they keep their original heading.
- **Author's notes stay with their chapter** – notices such as `작가의 말`, `[작가의 말]`, `후기`, `공지`, `Author's Note`, `A/N` or `Afterword` remain at the end of the chapter they follow. Tick **Split Author's Notes Into Own Chapters** (or use `--split-notes`) to make each one a separate chapter; either way they don't affect chapter numbering. Prologue, epilogue and side stories (`프롤로그`, `에필로그`, `외전`, `번외` …) are still split as chapters.
- **Missing chapters** – where a number is skipped (137 → 139), the app looks for an unlabelled chapter in between. If there is none, the log names the chapter that is not in the file instead of renumbering everything after it.
- **Noise is ignored** – scene breaks (`* * *`), inserted blocks (`//* … *//`), comment threads, sound effects and table-of-contents pages are not treated as chapter headings.

A book whose chapters are numbered 1, 2, 3… with no gaps is never split further, even when some chapters are long.

In **Separate Chapters** mode, each file is named after its chapter title (`#199화 신의 진노(3).txt`); a repeated title gets `(2)`, `(3)` … instead of overwriting. Tick **Number File Names** (`--number-files`) to add a sortable prefix (`199 #199화 신의 진노(3).txt`). It uses the chapter number when every chapter has its own, so a chapter missing from the source doesn't shift later file names. This is separate from **Number Titles**, which changes the titles themselves.

## Run from source

```
pip install -r requirements.txt
run.bat
```

### Command line

Headless batch mode: `python converter.py --cli [options] files-or-folders…`. The exe accepts the same `--cli` arguments, but it has no console window, so add `--log FILE` to see its output.

| Option | Meaning |
|---|---|
| `--formats txt,pdf,epub,csv` | Output formats (default `pdf`). |
| `--separate` | One file per chapter instead of one merged file. |
| `--spacing standard\|double\|remove` | Blank-line handling. |
| `--mode auto\|none\|numeric\|korean\|hash` | Chapter Format Mode. |
| `--example "…"` | Example heading for auto mode. |
| `--prefix "Ch.{n}"` | Chapter title template. |
| `--number-titles`, `--number-start N` | Number titles, starting at `N` (default 1). |
| `--number-files` | With `--separate`: put `001`, `002` … in front of chapter file names. |
| `--split-notes` | Make author's notes separate chapters. |
| `--no-subfolders` | Put all output straight into `Converted`. |
| `--toc`, `--keep-layout` | PDF table of contents; layout-preserving EPUB → PDF. |
| `--threads N` | Parallel threads, 1 up to your logical core count (default 2). |
| `--log FILE` | Also write the log to a file. |

## Build

`build.bat` produces `dist\TxTEPUBtoPDF-Converter.v<version>.exe` (e.g. `TxTEPUBtoPDF-Converter.v2.5.exe`) from `converter.spec`. The version comes from `APP_VERSION` in `converter.py`, and the publisher details (shirochi-stack) from `version_info.txt`.

The layout-preserving EPUB → PDF option needs the GTK runtime (MSYS2 `mingw64`); without it the app falls back to its built-in text PDF writer.
