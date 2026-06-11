"""Build the distributable zip for Texture Layer Manager.

Whitelist-based: only the files a user needs land in the zip, inside a
single texture_layer_manager/ folder so Blender installs it directly.
Run from the repo root:  python build_release.py
Output: _launch/TextureLayerManager_v<version>.zip
"""
import ast
import io
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PKG = "texture_layer_manager"

SHIP_FILES = [
    "__init__.py", "properties.py", "panels.py", "previews.py",
    "utils.py", "README.txt", "LICENSE.txt", "CHANGELOG.txt",
]
SHIP_DIRS = {
    "compositing": (".py",),
    "operators": (".py",),
    "presets": (".tlm",),
}


def version():
    with io.open(os.path.join(ROOT, "__init__.py"), encoding="utf-8") as f:
        m = re.search(r'"version":\s*\(([^)]+)\)', f.read())
    return ".".join(p.strip() for p in m.group(1).split(","))


def main():
    ver = version()
    out_dir = os.path.join(ROOT, "_launch")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"TextureLayerManager_v{ver}.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in SHIP_FILES:
            p = os.path.join(ROOT, name)
            if not os.path.isfile(p):
                raise SystemExit(f"missing shipped file: {name}")
            z.write(p, f"{PKG}/{name}")
            count += 1
        for d, exts in SHIP_DIRS.items():
            dp = os.path.join(ROOT, d)
            for fn in sorted(os.listdir(dp)):
                if fn.endswith(exts) and not fn.startswith("_DRAFT"):
                    z.write(os.path.join(dp, fn), f"{PKG}/{d}/{fn}")
                    count += 1
    size = os.path.getsize(out) / 1024.0
    print(f"{out}  ({count} files, {size:.0f} KB)")


if __name__ == "__main__":
    main()
