#!/usr/bin/env python3
"""
Install CascadiaPhotoelectric scripts into PixInsight's scripts directory.

Usage:
    python install.py
    python install.py --pi-dir "D:/PixInsight"
    python install.py --dry-run
"""

import argparse
import shutil
from pathlib import Path

DEFAULT_PI_DIR = Path("C:/Program Files/PixInsight")


def main():
    parser = argparse.ArgumentParser(description="Install CascadiaPhotoelectric PI scripts")
    parser.add_argument("--pi-dir", type=Path, default=DEFAULT_PI_DIR,
                        help=f"PixInsight install directory (default: {DEFAULT_PI_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be copied without copying")
    args = parser.parse_args()

    src_dir = Path(__file__).parent / "CascadiaPhotoelectric"
    dst_dir = args.pi_dir / "src" / "scripts" / "CascadiaPhotoelectric"

    if not src_dir.exists():
        print(f"ERROR: Source directory not found: {src_dir}")
        return 1

    if not args.pi_dir.exists():
        print(f"ERROR: PixInsight directory not found: {args.pi_dir}")
        print("Use --pi-dir to specify the correct path.")
        return 1

    scripts = list(src_dir.glob("*.js"))
    if not scripts:
        print(f"No .js files found in {src_dir}")
        return 1

    print(f"Source:      {src_dir}")
    print(f"Destination: {dst_dir}")
    print(f"Scripts:     {len(scripts)}")
    print()

    if not args.dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)

    for script in sorted(scripts):
        dst = dst_dir / script.name
        if args.dry_run:
            print(f"  [dry-run] {script.name} -> {dst}")
        else:
            shutil.copy2(script, dst)
            print(f"  Installed: {script.name}")

    if not args.dry_run:
        print()
        print("Done. In PixInsight:")
        print("  1. Script > Feature Scripts > Add")
        print(f"  2. Select: {dst_dir}")
        print("  3. Scripts appear under: Script > CascadiaPhotoelectric")
    else:
        print()
        print("[dry-run] No files copied.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
