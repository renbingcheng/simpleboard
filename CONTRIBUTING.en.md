# Contributing to SimpleBoard

[简体中文](CONTRIBUTING.md) | **English**

SimpleBoard focuses on local Windows handwriting, reliable saving, and verifiable Surface Pen behavior. See the [README](README.en.md) for run, test, and build commands. Implementation and regressions live in `whiteboard/` and `tests/`. The project uses [GPL-3.0-only](LICENSE); ensure you have the right to contribute under that license, and retain the actual licenses and attribution of third-party code and assets.

## Report an issue

A useful report includes:

- SimpleBoard version, and whether you used the single-file app or ran from source.
- Windows version and build. For input issues, include computer/Surface and pen models, drivers, and display scaling.
- Minimal reproduction steps, expected and actual behavior, and differences between mouse, pen tip, and eraser end.
- Whether it happens only in a particular board; where possible, a small `.qboard` example without private content.
- Screenshots or logs. For performance issues, include stroke count, first-open versus cached behavior, zoom, and whether recovery saving was enabled.

For input issues, press F12, reproduce the problem, export the JSON, and disable recording again. Records help distinguish native contact, Qt button conversion, and app cancellation; they cannot alone prove whether a physical pen tip left the screen. Check attachments for classroom content, private paths, real recovery drafts, or other sensitive data.

Do not submit personal settings, recovery files, executables, environments, caches, or the entire `artifacts/` tree. Regression samples should be small, public synthetic data with their source and purpose documented.

Root Markdown files and `docs/` remain local by default. Only documents and screenshots explicitly allowed by `.gitignore` are public. Internal development notes, reviews, references, and acceptance records are excluded from the repository and release packages. New public documents require updates to both ignore exceptions and the packaging inventory. Tests and build scripts remain public; third-party notices must be retained.

## Submit changes

Keep each change focused on a clear problem. Describe the triggering conditions and resulting behavior, and explain interactions for new features. For larger input, storage, or architecture changes, discuss the scope in an issue or draft first.

Before submitting:

1. Reproduce the issue or establish current behavior using Python 3.11 and pinned dependencies.
2. Update the relevant modules and add regressions for actual failure modes.
3. Run relevant tests. Run the full suite for shared core, input lifecycle, or saving changes.
4. Update README or CHANGELOG for visible changes. Document format compatibility changes and update validation and round-trip tests.
5. Report changes, validation commands, results, and device conditions that remain untested.

Pure layout or wording changes do not need tests duplicating the implementation, but check accessibility at 720 logical pixels wide. Touch targets must remain at least 44×44 logical pixels; long filenames must not displace controls.

## Implementation conventions

- Keep module responsibilities clear and follow existing Python style and type annotations. Avoid unrelated reformatting.
- Treat committed strokes, brushes, samples, masks, and images as immutable values. Moving and erasing replace objects instead of mutating shared lists; recovery workers and undo rely on this.
- Continuous movement during one contact is one operation. Real release, cancellation, loss of focus, or capture loss ends continuity. Do not infer that separate contacts should join using short delays or pressure thresholds.
- Failed saves must preserve unsaved work and valid existing files. Apply new limits consistently to loading and saving.
- Avoid synchronous disk access in input/painting paths and repeated whole-document geometry calculations per sample.
- Explain and pin new dependencies and add license source/checksum metadata. Consulting another open-source application does not authorize copying its code without its license requirements.
- Implement Windows native code against Microsoft SDK and Qt interfaces. Test structure layout, pointer width, history order, and cancellation boundaries.
- Use real structures and pointer wrappers for Windows binding tests, with injectable APIs for replaying boundaries. Similar-looking test doubles do not replace ABI checks.

## Translations and documentation

Simplified Chinese source strings are the translation keys. The English catalog is `whiteboard/translations.py`. Use full sentences and placeholders, for example `tr("已选择 {p0} 笔", p0=count)`; do not concatenate or format a sentence before translating. Register persistent controls with `TextBindings.bind()` for in-place language changes; newly created dialogs can call `tr()` directly. Add English entries for new text and run `tests/test_i18n.py`.

For another language, register its code and native name in `LANGUAGES` in `whiteboard/i18n.py`, add its catalog to `tr()`, and supply the Qt standard-dialog translation and packaging entry. Missing translations fall back to Chinese source text; unknown setting values fall back to `zh_CN`. Never translate `.qboard` keys, internal tool IDs, user filenames, or diagnostic JSON schema fields.

Maintain `README.md` / `README.en.md` and `CONTRIBUTING.md` / `CONTRIBUTING.en.md` together, retaining the language links at the top. Synchronize controls, compatibility limits, commands, and download links. Other documents currently retain their original language; English entry points mark Chinese documents explicitly. Preserve upstream license texts verbatim. Add new public documents to `.gitignore` exceptions and `scripts/public_distribution.py` so source, single-file, and directory packages include them.

Verify that changing language preserves strokes, images, undo history, selection, and view. Check untitled titles, save/recovery prompts, export filters, and diagnostics. English text is often longer: inspect the 720×560 minimum window and tool popovers with bottom, left, and right toolbars, including both sidebar column layouts. Catalog coverage alone is insufficient.

Check “Select image” (`S`) separately from Lasso (`L`): ordinary pens, the lasso, and temporary pen-side-button selection must not move or resize images. Selecting ink over an image must preserve the background. The global pressure toggle lives in the main menu; sensitivity stays in the pen popover and is disabled when pressure is off.

Run the full regression suite with `QT_QPA_PLATFORM=offscreen`. Before release, run the final EXE with `--smoke-test --smoke-output <directory>` to check native Windows execution, PNG/JPEG import, save/reopen after deleting the original files, image-transform undo, and PNG/PDF exports. Keep `qjpeg.dll` in the build; JPEG support in development does not prove the packaged app includes the plugin. Local reports in `artifacts/` are excluded from source commits.

## Describe validation accurately

Distinguish offscreen tests, native Windows windows, system synthetic input, and physical Surface tests. Write “not tested” when hardware is unavailable; do not turn synthetic results into hardware compatibility claims. Input changes especially need checks for light/heavy pressure, fast pen lifts, continuous eraser-end movement and a single undo, palm rejection, side button, rotation, DPI, and suspend/resume.

Performance reports should include CPU, DPI, window size, document size, cache state, recovery setting, p95, maximum timing, and commit timing. Paint time is not total pen-to-screen latency.
