Third-party runtime license and attribution notices
==================================================

This directory accompanies SimpleBoard. It contains
verbatim upstream license texts and attribution metadata for Qt / PySide6 /
Shiboken6 6.11.1, Python 3.11.2, and the PyInstaller runtime/bootloader used by the
reference build, plus pyclipper 1.4.0 / Clipper 6.4.2 notices. The application
uses dynamic Qt libraries. Their layout and the user's modification route
depend on the packaging mode; see the distribution limits below.

Qt and Qt for Python
--------------------
The open-source LGPL v3 option is used for the applicable Qt Core, GUI, Widgets
and Qt for Python runtime libraries. The LGPL
v3, GPL v3 (incorporated by reference in LGPL v3), Qt exceptions and upstream
third-party notices are included. Other license alternatives included in the
upstream LICENSES directories are retained as upstream reference texts; their
presence does not claim that this project has a commercial Qt license or uses
every corresponding component. Retained SVG and image-format notices do not
mean those optional modules are present in the current binary.

Sources for the exact upstream release are available from:
https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/
https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/
https://github.com/qt/qtbase/tree/v6.11.1
https://github.com/qt/qtsvg/tree/v6.11.1
https://github.com/qt/qtimageformats/tree/v6.11.1
https://github.com/pyside/pyside-setup/tree/v6.11.1

Third-party copyright statements, license identifiers, versions and license-file
references are preserved in each qt_attribution.json. Referenced non-source
license files are placed alongside those metadata files. The collected notices
are a conservative set for these upstream modules; some concern optional build
features or platforms and do not imply those components are present in the
Windows executable. This is an attribution inventory, not a binary-derived SBOM.
The Python 3.7 PSF agreement embedded in Shiboken's bufferprocs_py37.h is supplied
by the separately published PSF-3.7.0.txt in its signature notices; the upstream
copyright and original file reference remain in the attribution metadata.

Python and PyInstaller
----------------------
python/LICENSE is the Python release's upstream license. The Windows distribution
LICENSE.txt includes its dependency notices and is retained in full, including
notices for optional standard-library modules excluded from this application.
pyinstaller/COPYING.txt includes the PyInstaller bootloader exception and its
runtime-hook licensing terms. Apache-2.0 and GPL-2.0 license texts are included
under the Qt upstream LICENSES directories as well.

pyclipper/LICENSE contains its MIT license. pyclipper/clipper/ contains the
Clipper 6.4.2 upstream copyright declaration and Boost Software License 1.0.

Verification and reproducibility
-------------------------------
sources.json records exact source URLs, versions/tags, upstream tree identities,
file sizes and SHA-256 digests. The developer-only scripts/prepare_licenses.py
verifies the local files without network by default. Explicit refresh options
collect notices from official upstream sources. The Qt and pyclipper tags are
pinned; a full refresh takes Python/PyInstaller versions from the executing
environment, so review those against the actual binary before publishing.
The whiteboard app does not import or run that collection script and remains
offline. Include this entire directory, preserving subdirectories, in each
portable distribution and keep a directly readable copy with release materials.

Distribution limits
-------------------
This is a notice inventory, not complete corresponding source or a binary SBOM.
The upstream URL and tree records help locate sources; they do not replace a
publisher's applicable source-delivery obligations. SimpleBoard application
source alone does not include all third-party library source or build materials.

Onedir places dynamic libraries under _internal. Onefile embeds them and extracts
them into a temporary directory. Temporary extraction is not by itself a stable,
verified way to replace a library. Neither packaging mode alone establishes
LGPL compliance. Publishers must provide and validate the applicable library
replacement/recombination, source and installation materials; see
docs/THIRD_PARTY.md in the project/release documentation.

SimpleBoard's own GPL-3.0-only license is in the root LICENSE. It does not replace
the separate licenses and notices preserved in this directory. Do not alter
upstream license texts when updating project documentation.
