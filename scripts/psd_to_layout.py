#!/usr/bin/env python3
"""从规范 PSD 生成 layout_presets 模板 JSON。

用法:
  python scripts/psd_to_layout.py /path/to/规范.psd [pack_id] [template_id]

示例:
  python scripts/psd_to_layout.py ~/Desktop/一体机规范.psd hll-banner-extend yitiji
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from layout_extend import LAYOUT_PRESETS_DIR, parse_psd_layout  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    psd_path = Path(sys.argv[1]).expanduser().resolve()
    if not psd_path.is_file():
        print(f"文件不存在: {psd_path}")
        return 1

    pack_id = sys.argv[2] if len(sys.argv) > 2 else "hll-banner-extend"
    template_id = sys.argv[3] if len(sys.argv) > 3 else psd_path.stem

    tpl = parse_psd_layout(psd_path)
    tpl["id"] = template_id

    pack_dir = LAYOUT_PRESETS_DIR / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    out_path = pack_dir / f"{template_id}.json"
    out_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = pack_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "id": pack_id,
            "name": pack_id,
            "description": "规范延展模板包",
            "templates": [],
        }
    templates = list(manifest.get("templates") or [])
    if template_id not in templates:
        templates.append(template_id)
    manifest["templates"] = templates
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已写入: {out_path}")
    print(json.dumps(tpl, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
