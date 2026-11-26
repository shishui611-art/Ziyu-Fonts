#!/usr/bin/env python3
"""Build Ziyu-Fonts catalog (fonts.json), specimen previews, and Release ZIP packages.

Only open-source licensed fonts defined in sources.json are processed.
Requires: Python 3.10+, fontTools, Pillow; optional 7z CLI for .7z archives.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = ROOT / ".work"
PREVIEWS_DIR = ROOT / "previews" / "v2"
PREVIEWS_REL = "previews/v2"
RELEASES_DIR = ROOT / "dist-releases"
SOURCES_FILE = ROOT / "sources.json"
FONTS_JSON = ROOT / "fonts.json"

CJK_UI_PROBES = tuple(map(ord, "中文字体系统默认洛书汉字ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"))
ALLOWED_LICENSES = ("ofl", "open font license", "apache", "mit", "bsd", "gpl", "mpl", "cc0", "ipa")

WEIGHT_LABELS = (
    (150, "Thin"),
    (250, "ExtraLight"),
    (350, "Light"),
    (450, "Regular"),
    (550, "Medium"),
    (650, "SemiBold"),
    (750, "Bold"),
    (850, "ExtraBold"),
    (1001, "Black"),
)

DEFAULT_PROXIES = [
    "",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
]


def weight_label(weight_class: int) -> str:
    for limit, label in WEIGHT_LABELS:
        if weight_class < limit:
            return label
    return "Black"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path, proxies: list[str], force: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0 and not force:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    curl_bin = shutil.which("curl.exe") or shutil.which("curl")
    last_err: Exception | None = None
    for prefix in proxies:
        full_url = f"{prefix}{url}" if prefix else url
        for attempt in range(3):
            try:
                if curl_bin:
                    cmd = [
                        curl_bin, "-fSL", "--connect-timeout", "20", "--max-time", "600",
                        "--retry", "3", "--retry-delay", "2", "-C", "-",
                        "-A", "Ziyu-Fonts-Builder/1.0", "-o", str(tmp), full_url,
                    ]
                    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
                    if res.returncode != 0:
                        raise RuntimeError(f"curl exit {res.returncode}: {res.stderr.strip()[:200]}")
                    if not tmp.is_file() or tmp.stat().st_size <= 0:
                        raise RuntimeError("Empty curl download")
                    tmp.replace(dest)
                    return dest
                req = urllib.request.Request(full_url, headers={"User-Agent": "Ziyu-Fonts-Builder/1.0"})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    expected_header = resp.headers.get("Content-Length")
                    expected_len = int(expected_header) if expected_header and expected_header.isdigit() else None
                    written = 0
                    with tmp.open("wb") as out:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            written += len(chunk)
                if written <= 0 or (expected_len is not None and written != expected_len):
                    raise RuntimeError(f"Incomplete download: {written}/{expected_len}")
                tmp.replace(dest)
                return dest
            except Exception as exc:
                last_err = exc
                if not curl_bin:
                    tmp.unlink(missing_ok=True)
                time.sleep(1.0)
        tmp.unlink(missing_ok=True)
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def find_7z() -> str | None:
    for cand in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if cand and Path(cand).is_file():
            return cand
    return None


def extract_matching_fonts(archive: Path, out_dir: Path, regex_text: str, fallback_regex: str | None = None) -> list[Path]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_text)
    fallback = re.compile(fallback_regex) if fallback_regex else None
    extracted: list[Path] = []

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            matched = [m for m in members if pattern.search(m.filename.replace("\\", "/"))]
            if not matched and fallback:
                matched = [m for m in members if fallback.search(m.filename.replace("\\", "/"))]
            if not matched:
                sample = [m.filename for m in members if m.filename.lower().endswith((".ttf", ".otf"))][:20]
                raise RuntimeError(f"No font matched {regex_text} in {archive.name}; sample={sample}")
            seen_names: set[str] = set()
            for member in matched:
                base = Path(member.filename).name
                if base.lower() in seen_names:
                    continue
                seen_names.add(base.lower())
                target = out_dir / base
                with zf.open(member, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
    elif archive.suffix.lower() == ".7z":
        seven_z = find_7z()
        if not seven_z:
            raise RuntimeError("7z CLI is required to unpack .7z archives")
        list_out = subprocess.check_output([seven_z, "l", "-slt", str(archive)], text=True, encoding="utf-8", errors="replace")
        paths = [line.split(" = ", 1)[1].strip() for line in list_out.splitlines() if line.startswith("Path = ")]
        matched = [p for p in paths[1:] if pattern.search(p.replace("\\", "/"))]
        if not matched and fallback:
            matched = [p for p in paths[1:] if fallback.search(p.replace("\\", "/"))]
        if not matched:
            raise RuntimeError(f"No font matched {regex_text} in {archive.name}")
        cmd = [seven_z, "e", "-y", f"-o{out_dir}", str(archive), *matched]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        extracted = sorted(p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in (".ttf", ".otf"))
    else:
        raise RuntimeError(f"Unsupported archive format: {archive.name}")

    return sorted(extracted)


def inspect_font(path: Path) -> dict:
    tt = TTFont(str(path), lazy=True, recalcTimestamp=False)
    try:
        try:
            family = tt["name"].getBestFamilyName() or tt["name"].getDebugName(1) or path.stem
        except Exception:
            family = path.stem
        try:
            subfamily = tt["name"].getBestSubFamilyName() or tt["name"].getDebugName(2) or "Regular"
        except Exception:
            subfamily = "Regular"
        weight = int(tt["OS/2"].usWeightClass) if "OS/2" in tt else 400
        weight = max(1, min(1000, weight))
        italic = bool(int(tt["head"].macStyle) & 0x02) if "head" in tt else False
        variable = "fvar" in tt
        cmap = tt.getBestCmap() or {}
        supports_cjk = all(cp in cmap for cp in CJK_UI_PROBES)
        fmt = "OTF" if tt.sfntVersion == "OTTO" else "TTF"
        axes = [a.axisTag for a in tt["fvar"].axes] if variable else []
        return {
            "path": path,
            "family": family,
            "subfamily": subfamily,
            "weight": weight,
            "weightLabel": weight_label(weight),
            "italic": italic,
            "variable": variable,
            "axes": axes,
            "supportsCjk": supports_cjk,
            "format": fmt,
        }
    finally:
        tt.close()


def choose_preview_font(infos: list[dict]) -> dict:
    for item in infos:
        if item["variable"]:
            return item
    return min(infos, key=lambda item: (abs(item["weight"] - 400), item["weight"]))


def find_ui_fallback_font(bold: bool = False) -> str | None:
    candidates = (
        [
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ]
        if bold
        else [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
    )
    for path in candidates:
        if Path(path).is_file():
            return path
    for base in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        if base.is_dir():
            for hit in sorted(base.rglob("*NotoSansCJK*.ttc")) + sorted(base.rglob("*NotoSansCJK*.otf")):
                if hit.is_file():
                    return str(hit)
    fc_match = shutil.which("fc-match")
    if fc_match:
        try:
            out = subprocess.check_output([fc_match, "-f", "%{file}", "Noto Sans CJK SC"], text=True).strip()
            if out and Path(out).is_file():
                return out
        except Exception:
            pass
    return None


def load_specimen_font(font_path: Path, size: int, variable: bool, weight: int | None = None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(font_path), size=size)
    if variable and weight is not None:
        try:
            axes = font.get_variation_axes()
            coords = []
            for axis in axes:
                tag = axis.get("name", b"").decode("latin1", errors="ignore") if isinstance(axis.get("name"), bytes) else str(axis.get("name", ""))
                default = axis.get("default", 400)
                lo = axis.get("minimum", default)
                hi = axis.get("maximum", default)
                if "weight" in tag.lower() or len(axes) == 1:
                    coords.append(max(lo, min(hi, weight)))
                else:
                    coords.append(default)
            if coords:
                font.set_variation_by_axes(coords)
        except Exception:
            pass
    return font


def render_preview(entry: dict, preview_info: dict, all_infos: list[dict], out_path: Path, dark: bool) -> None:
    """Render a minimalist transparent-background single-line specimen (720x450, 1.6:1).

    Transparent RGBA lets the card adapt seamlessly to any user theme (Light, Dark,
    AMOLED Pure Black, Material You, Miuix, COUI, or dynamic wallpaper).
    """
    width, height = 720, 450
    fg = (242, 244, 248, 255) if dark else (24, 28, 36, 255)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    reg_path = preview_info["path"]
    is_var = preview_info["variable"]
    default_specimen = "海上升明月" if preview_info["supportsCjk"] else "Hello 09:41"
    specimen = (entry.get("specimen") or default_specimen).strip()

    # Auto-fit single centered line with generous horizontal breathing room
    max_text_width = width - 84
    start_size = 98 if len(specimen) <= 5 else 82
    chosen_font = load_specimen_font(reg_path, start_size, is_var, 450)
    bbox = draw.textbbox((0, 0), specimen, font=chosen_font)
    for size in range(start_size, 34, -4):
        cand = load_specimen_font(reg_path, size, is_var, 450)
        b = draw.textbbox((0, 0), specimen, font=cand)
        if (b[2] - b[0]) <= max_text_width:
            chosen_font = cand
            bbox = b
            break

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) / 2.0 - bbox[0]
    y = (height - text_h) / 2.0 - bbox[1]
    draw.text((x, y), specimen, fill=fg, font=chosen_font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)


def write_release_zip(entry: dict, font_files: list[Path], license_file: Path, out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = out_zip.with_suffix(".zip.part")
    module_prop = (
        f"id=ziyu-font-{entry['id']}\n"
        f"name={entry['name']}\n"
        f"version={entry['version']}\n"
        f"versionCode=1\n"
        f"author={entry['author']}\n"
        f"description={entry['license']} | {entry['repo']}\n"
    ).encode("utf-8")

    fixed_date = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zi = zipfile.ZipInfo("module.prop", date_time=fixed_date)
        zi.external_attr = 0o644 << 16
        zf.writestr(zi, module_prop)

        li = zipfile.ZipInfo("LICENSE", date_time=fixed_date)
        li.external_attr = 0o644 << 16
        zf.writestr(li, license_file.read_bytes())

        for font_path in font_files:
            fi = zipfile.ZipInfo(f"fonts/{font_path.name}", date_time=fixed_date)
            fi.external_attr = 0o644 << 16
            zf.writestr(fi, font_path.read_bytes())

    tmp_zip.replace(out_zip)


def build_entry(entry: dict, release_tag: str, repo_slug: str, proxies: list[str], force_download: bool) -> dict:
    font_id = entry["id"]
    lic_lower = entry["license"].lower()
    if not any(k in lic_lower for k in ALLOWED_LICENSES):
        raise ValueError(f"{font_id}: license {entry['license']!r} is not in open-source allowlist")

    item_work = WORK_DIR / font_id
    item_work.mkdir(parents=True, exist_ok=True)
    license_file = download_file(entry["licenseUrl"], item_work / "LICENSE", proxies, force=force_download)

    if "directFileUrl" in entry:
        raw_name = Path(entry["directFileUrl"].split("?")[0]).name
        downloaded = download_file(entry["directFileUrl"], item_work / raw_name, proxies, force=force_download)
        extract_dir = item_work / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        copied = extract_dir / raw_name
        shutil.copy2(downloaded, copied)
        font_files = [copied]
    else:
        archive_name = Path(entry["archiveUrl"].split("?")[0]).name
        archive_file = download_file(entry["archiveUrl"], item_work / archive_name, proxies, force=force_download)
        font_files = extract_matching_fonts(
            archive_file,
            item_work / "extracted",
            entry["includeRegex"],
            entry.get("fallbackRegex"),
        )

    if entry.get("renameSingleTo") and len(font_files) == 1:
        renamed = font_files[0].with_name(entry["renameSingleTo"])
        if renamed != font_files[0]:
            font_files[0].rename(renamed)
            font_files = [renamed]

    infos = [inspect_font(f) for f in font_files]

    # Guard: ensure Ziyu's font_import.sh won't reject all files as italic/oblique
    usable = [
        i for i in infos
        if not i["italic"] and "italic" not in i["path"].name.lower() and "oblique" not in i["path"].name.lower()
    ]
    if not usable:
        raise RuntimeError(
            f"{font_id}: no non-italic upright font found after inspection: "
            + ", ".join(f"{i['path'].name}(italic={i['italic']})" for i in infos)
        )
    infos = sorted(usable, key=lambda x: (x["weight"], x["path"].name))
    font_files = [i["path"] for i in infos]

    # Verify unified family name for multi-weight static packages
    families = {i["family"] for i in infos}
    if len(infos) >= 2 and len(families) > 1:
        primary_fam = max(families, key=lambda f: sum(1 for i in infos if i["family"] == f))
        infos = [i for i in infos if i["family"] == primary_fam]
        font_files = [i["path"] for i in infos]

    preview_info = choose_preview_font(infos)
    light_png = PREVIEWS_DIR / f"{font_id}.png"
    dark_png = PREVIEWS_DIR / f"{font_id}-dark.png"
    render_preview(entry, preview_info, infos, light_png, dark=False)
    render_preview(entry, preview_info, infos, dark_png, dark=True)

    out_zip = RELEASES_DIR / f"{font_id}.zip"
    write_release_zip(entry, font_files, license_file, out_zip)

    zip_size = out_zip.stat().st_size
    zip_sha = sha256_file(out_zip)
    is_variable = any(i["variable"] for i in infos)
    weights = ["Variable"] if is_variable else list(dict.fromkeys(i["weightLabel"] for i in infos))
    supports_cjk = any(i["supportsCjk"] for i in infos)
    fmt = preview_info["format"]
    download_path = f"releases/download/{release_tag}/{out_zip.name}"

    return {
        "id": font_id,
        "name": entry["name"],
        "englishName": entry["englishName"],
        "author": entry["author"],
        "category": "cjk" if supports_cjk else "latin",
        "style": entry["style"],
        "description": entry["description"],
        "license": entry["license"],
        "repo": entry["repo"],
        "stars": int(entry.get("stars", 0)),
        "version": entry["version"],
        "supportsCjk": supports_cjk,
        "variable": is_variable,
        "weights": weights,
        "format": fmt,
        "preview": f"{PREVIEWS_REL}/{light_png.name}",
        "previewDark": f"{PREVIEWS_REL}/{dark_png.name}",
        "downloadPath": download_path,
        "downloadUrl": f"https://github.com/{repo_slug}/{download_path}",
        "size": zip_size,
        "sha256": zip_sha,
        "tags": entry.get("tags", []),
    }


def repreview_from_zips(config: dict) -> int:
    """Re-render previews directly from existing dist-releases/*.zip while preserving live fonts.json hashes."""
    if not FONTS_JSON.is_file():
        raise FileNotFoundError("fonts.json is required for --repreview-from-zips")
    live = json.loads(FONTS_JSON.read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in config["fonts"]}
    tmp_root = WORK_DIR / "_repreview"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    try:
        for item in live.get("fonts", []):
            fid = item["id"]
            entry = by_id.get(fid, item)
            zpath = RELEASES_DIR / f"{fid}.zip"
            if not zpath.is_file():
                raise FileNotFoundError(f"Missing local ZIP: {zpath}")
            out_dir = tmp_root / fid
            out_dir.mkdir(parents=True, exist_ok=True)
            font_files: list[Path] = []
            with zipfile.ZipFile(zpath, "r") as zf:
                for member in zf.infolist():
                    if member.is_dir() or not member.filename.lower().endswith((".ttf", ".otf")):
                        continue
                    dst = out_dir / Path(member.filename).name
                    with zf.open(member, "r") as src, dst.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    font_files.append(dst)
            infos = sorted((inspect_font(f) for f in font_files), key=lambda x: (x["weight"], x["path"].name))
            preview_info = choose_preview_font(infos)
            light_png = PREVIEWS_DIR / f"{fid}.png"
            dark_png = PREVIEWS_DIR / f"{fid}-dark.png"
            render_preview(entry, preview_info, infos, light_png, dark=False)
            render_preview(entry, preview_info, infos, dark_png, dark=True)
            item["preview"] = f"{PREVIEWS_REL}/{light_png.name}"
            item["previewDark"] = f"{PREVIEWS_REL}/{dark_png.name}"
            print(f"  Rendered v2 preview: {fid} -> {entry.get('specimen', '')}", flush=True)
        live["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        FONTS_JSON.write_text(json.dumps(live, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {FONTS_JSON} previews -> {PREVIEWS_REL}/ (ZIP hashes preserved).")
        return 0
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="Build only specific font IDs")
    parser.add_argument("--proxy", default="", help="Preferred GitHub proxy prefix, e.g. https://ghfast.top/")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--clean-work", action="store_true", help="Remove .work download cache after success")
    parser.add_argument("--repreview-from-zips", action="store_true", help="Re-render previews from existing dist-releases/*.zip without changing ZIP hashes")
    args = parser.parse_args()

    config = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    if args.repreview_from_zips:
        return repreview_from_zips(config)
    release_tag = config["releaseTag"]
    repo_slug = config["repository"]
    proxies = [args.proxy.rstrip("/") + "/"] + DEFAULT_PROXIES if args.proxy else list(DEFAULT_PROXIES)

    existing_by_id: dict[str, dict] = {}
    if FONTS_JSON.is_file() and args.only:
        old = json.loads(FONTS_JSON.read_text(encoding="utf-8"))
        for item in old.get("fonts", []):
            existing_by_id[item["id"]] = item

    selected = set(args.only or [])
    catalog_entries: list[dict] = []
    failed: list[tuple[str, str]] = []

    for idx, entry in enumerate(config["fonts"], start=1):
        fid = entry["id"]
        if selected and fid not in selected:
            if fid in existing_by_id:
                catalog_entries.append(existing_by_id[fid])
            continue
        print(f"[{idx}/{len(config['fonts'])}] Building {fid} ({entry['name']})...", flush=True)
        try:
            built = build_entry(entry, release_tag, repo_slug, proxies, args.force_download)
            catalog_entries.append(built)
            print(
                f"  OK: {built['format']} cjk={built['supportsCjk']} var={built['variable']} "
                f"weights={built['weights']} zip={built['size']/1048576:.2f}MB sha={built['sha256'][:12]}",
                flush=True,
            )
        except Exception as exc:
            print(f"  FAILED {fid}: {exc}", file=sys.stderr, flush=True)
            failed.append((fid, str(exc)))

    # Sort: CJK first (by stars desc), then Latin/Digit (by stars desc)
    catalog_entries.sort(key=lambda x: (0 if x["supportsCjk"] else 1, -int(x.get("stars", 0)), x["id"]))

    payload = {
        "schema": 1,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": repo_slug,
        "releaseTag": release_tag,
        "notice": config.get("notice", ""),
        "fonts": catalog_entries,
    }
    FONTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {FONTS_JSON} with {len(catalog_entries)} fonts.", flush=True)

    if failed:
        print(f"WARNING: {len(failed)} font(s) failed:", file=sys.stderr)
        for fid, reason in failed:
            print(f"  - {fid}: {reason}", file=sys.stderr)
        return 1

    if args.clean_work and WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
        print("Cleaned .work/ cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
