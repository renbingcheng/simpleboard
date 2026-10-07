"""Developer-only collection of pinned upstream license/attribution notices.

This script is never imported by the app. Ordinary packaging uses the checked-in
licenses directory and needs no network. --refresh permits fetching notice text
from official upstream sources; only license and attribution text is retained.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path, PurePosixPath
import posixpath
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
QT_TAG = "v6.11.1"
REPOSITORIES = ("qt/qtbase", "qt/qtsvg", "qt/qtimageformats", "pyside/pyside-setup")
PYCLIPPER_VERSION = "1.4.0"


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SimpleBoard-license-notice-collector/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def desired_attribution(repository: str, path: str) -> bool:
    if not path.endswith("qt_attribution.json"):
        return False
    if repository == "pyside/pyside-setup":
        return path.startswith("sources/shiboken6/")
    if repository != "qt/qtbase":
        return path.startswith("src/")
    excluded = ("/wayland/", "/android/", "/gradle/", "/wasm/", "/forkfd/", "/xcb/", "/sqlite/", "/libpsl/")
    return (not any(part in path for part in excluded)
            and path.startswith(("src/3rdparty/", "src/corelib/", "src/gui/", "util/gradientgen/")))


def collect_pyclipper(destination: Path) -> list[dict]:
    """Preserve the installed MIT notice and pinned Clipper attribution."""
    package = distribution("pyclipper")
    if package.version != PYCLIPPER_VERSION:
        raise RuntimeError(f"Expected pyclipper {PYCLIPPER_VERSION}, found {package.version}")
    license_file = next(entry for entry in package.files or ()
                        if entry.name == "LICENSE" and ".dist-info/" in str(entry))
    installed = Path(package.locate_file(license_file)).read_bytes()
    base_url = f"https://raw.githubusercontent.com/fonttools/pyclipper/{PYCLIPPER_VERSION}/"
    sources = {
        "LICENSE": (base_url + "LICENSE", "32ee7398e3123736fd14e9f08287edc2d97ace102ab54d8dc4edc8d31c651fa8"),
        "header": (base_url + "src/clipper.hpp", "734eba9dc9d399089b2b467017074bd24728a1b9e64c7429e827806ed10e54cc"),
        "boost": ("https://www.boost.org/LICENSE_1_0.txt", "c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566"),
    }
    fetched = {}
    for key, (url, expected) in sources.items():
        data = download(url)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Pinned pyclipper license source changed: {url}")
        fetched[key] = data
    if installed.replace(b"\r\n", b"\n") != fetched["LICENSE"].replace(b"\r\n", b"\n"):
        raise RuntimeError("Installed pyclipper MIT license differs from the pinned upstream release")
    header = fetched["header"]
    if not header.startswith(b"/*") or b"*/" not in header:
        raise RuntimeError("Clipper header is missing its leading attribution declaration")
    notice = header[:header.index(b"*/") + 2]
    entries = []

    def save(relative, data, source, component, origin, **metadata):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        url, source_hash = sources[source]
        entries.append({"file": relative, "component": component, "source_url": url,
                        "origin": origin, "source_sha256": source_hash,
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), **metadata})

    save("pyclipper/LICENSE", installed, "LICENSE", f"pyclipper {PYCLIPPER_VERSION}",
         "installed-runtime-license", distribution_file=str(license_file), license="MIT",
         upstream_comparison="Identical after CRLF-to-LF normalization; installed bytes preserved.")
    save("pyclipper/clipper/NOTICE.txt", notice, "header", "Clipper 6.4.2 (pyclipper 1.4.0)",
         "upstream-tag-header-excerpt", extraction="Complete leading /* ... */ comment, verbatim.",
         license="BSL-1.0", copyright="Angus Johnson 2010-2017")
    save("pyclipper/clipper/LICENSE_1_0.txt", fetched["boost"], "boost",
         "Clipper 6.4.2 (pyclipper 1.4.0)", "official-license-text", license="BSL-1.0",
         attribution_file="pyclipper/clipper/NOTICE.txt")
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch pinned license notices from official upstream sources")
    parser.add_argument("--refresh-pyclipper", action="store_true",
                        help="Refresh only pyclipper/Clipper notices, preserving the existing manifest")
    args = parser.parse_args(argv)
    destination = ROOT / "licenses"
    manifest_path = destination / "sources.json"
    if not args.refresh:
        if not manifest_path.exists():
            parser.error("licenses/sources.json is missing; run once with --refresh")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = destination / item["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError(f"License notice checksum mismatch: {path}")
        if args.refresh_pyclipper:
            entries = collect_pyclipper(destination)
            manifest["files"] = sorted(
                [item for item in manifest["files"] if not item["file"].startswith("pyclipper/")] + entries,
                key=lambda entry: entry["file"])
            manifest["pyclipper_collected_at_utc"] = datetime.now(timezone.utc).isoformat()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Prepared {len(entries)} pinned pyclipper/Clipper notices; other entries preserved.")
            return 0
        print(f"Verified {len(manifest['files'])} local license/attribution files; no network used.")
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    trees = {}
    tree_sources = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        requests = [(repo, f"https://api.github.com/repos/{repo}/git/trees/{QT_TAG}?recursive=1") for repo in REPOSITORIES]
        responses = pool.map(download, [url for _, url in requests])
        for (repo, url), data in zip(requests, responses):
            value = json.loads(data)
            if value.get("truncated"):
                raise RuntimeError(f"Incomplete upstream tree: {repo}")
            trees[repo] = {entry["path"] for entry in value["tree"] if entry["type"] == "blob"}
            tree_sources.append({"repository": repo, "tag": QT_TAG, "tree_sha": value["sha"], "url": url})

    requests = []
    for repo, paths in trees.items():
        for path in sorted(paths):
            if path.startswith("LICENSES/") or desired_attribution(repo, path):
                requests.append((repo, path))
    fetched = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        urls = [f"https://raw.githubusercontent.com/{repo}/{QT_TAG}/{path}" for repo, path in requests]
        for key, data in zip(requests, pool.map(download, urls)):
            fetched[key] = data

    missing = []
    embedded = []
    referenced = set()
    for (repo, path), data in list(fetched.items()):
        if not path.endswith("qt_attribution.json"):
            continue
        # Some Qt attribution files contain literal newlines inside descriptive
        # fields; retain their bytes verbatim while tolerating that metadata.
        attribution = json.loads(data, strict=False)
        entries = attribution if isinstance(attribution, list) else [attribution]
        for entry in entries:
            filenames = entry.get("LicenseFile", [])
            filenames = [filenames] if isinstance(filenames, str) else filenames
            for filename in filenames:
                base = posixpath.dirname(path)
                candidates = [posixpath.normpath(posixpath.join(base, filename))]
                if isinstance(entry.get("Path"), str):
                    candidates.append(posixpath.normpath(posixpath.join(base, entry["Path"], filename)))
                found = next((candidate for candidate in candidates if candidate in trees[repo]), None)
                if found is None:
                    missing.append({"repository": repo, "attribution": path, "license_file": filename})
                elif PurePosixPath(found).suffix.lower() in (".c", ".cpp", ".h", ".hpp", ".py", ".js"):
                    # Retain the upstream copyright/license declaration in the
                    # attribution JSON, but do not download executable source.
                    equivalent = "sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/PSF-3.7.0.txt"
                    if repo == "pyside/pyside-setup" and filename == "bufferprocs_py37.h" and equivalent in trees[repo]:
                        embedded.append({"repository": repo, "attribution": path, "license_file": filename,
                                         "included_license": f"pyside-setup/{equivalent}",
                                         "reason": "Python 3.7 PSF agreement supplied as the separate upstream license text; source code was not downloaded."})
                        referenced.add((repo, equivalent))
                    else:
                        missing.append({"repository": repo, "attribution": path, "license_file": filename,
                                        "reason": "License is embedded in source; attribution and SPDX license text are included."})
                else:
                    referenced.add((repo, found))
    requests = sorted(referenced - fetched.keys())
    with ThreadPoolExecutor(max_workers=8) as pool:
        urls = [f"https://raw.githubusercontent.com/{repo}/{QT_TAG}/{path}" for repo, path in requests]
        for key, data in zip(requests, pool.map(download, urls)):
            fetched[key] = data

    entries = []

    def save(relative: str, data: bytes, url: str, component: str, origin="upstream-tag"):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({"file": relative, "component": component, "source_url": url, "origin": origin,
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    for (repo, path), data in sorted(fetched.items()):
        save(f"{repo.rsplit('/', 1)[1]}/{path}", data,
             f"https://raw.githubusercontent.com/{repo}/{QT_TAG}/{path}", f"{repo} {QT_TAG}")

    python_version = ".".join(map(str, sys.version_info[:3]))
    python_url = f"https://raw.githubusercontent.com/python/cpython/v{python_version}/LICENSE"
    save("python/LICENSE", download(python_url), python_url, f"CPython {python_version}")
    # The Windows installer combines dependency notices with the Python license.
    installed_python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if installed_python_license.is_file():
        save("python/WINDOWS-DISTRIBUTION-LICENSE.txt", installed_python_license.read_bytes(),
             f"https://www.python.org/ftp/python/{python_version}/python-{python_version}-amd64.exe",
             f"CPython Windows distribution {python_version}", "installed-runtime-license")
    pyinstaller = distribution("pyinstaller")
    copying = next(entry for entry in pyinstaller.files if entry.name == "COPYING.txt")
    save("pyinstaller/COPYING.txt", Path(pyinstaller.locate_file(copying)).read_bytes(),
         f"https://raw.githubusercontent.com/pyinstaller/pyinstaller/v{pyinstaller.version}/COPYING.txt",
         f"PyInstaller {pyinstaller.version}", "installed-runtime-license")
    entries.extend(collect_pyclipper(destination))

    manifest = {"collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "purpose": "Offline runtime license texts and upstream attribution notices; not a per-binary SBOM.",
                "upstream_trees": tree_sources, "files": sorted(entries, key=lambda entry: entry["file"]),
                "embedded_license_references": embedded, "unresolved_references": missing}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(entries)} license/attribution files; {len(missing)} references need inspection.")
    for item in missing:
        print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
