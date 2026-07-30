# backend/tests/test_project_reference_listing.py
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import product_design
from app import _build_image_paths_from_selection


class ProjectReferenceListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        xdt = self.root / "小灯塔"
        xdt.mkdir()
        (xdt / "project.json").write_text(
            json.dumps({"catalog": "static_types", "product_type": "xdt"}),
            encoding="utf-8",
        )
        (xdt / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        hll = self.root / "画啦啦"
        (hll / "types" / "17-封面").mkdir(parents=True)
        (hll / "project.json").write_text(
            json.dumps({"catalog": "folder_types", "product_type": "hll"}),
            encoding="utf-8",
        )
        (hll / "types" / "17-封面" / "cover.jpg").write_bytes(b"JPEGFAKE")

        self._projects_patch = patch.object(product_design, "PROJECTS_DIR", self.root)
        self._projects_patch.start()
        self.addCleanup(self._projects_patch.stop)

    def test_list_flat_images_for_xdt_root(self):
        names = product_design.list_flat_reference_images("小灯塔")
        self.assertEqual(names, ["poster.png"])

    def test_list_typed_images_for_hll(self):
        names = product_design.list_typed_reference_images("画啦啦", "17-封面")
        self.assertEqual(names, ["cover.jpg"])

    def test_selected_xdt_image_resolves(self):
        fields = {
            "selected_project_images": json.dumps(["小灯塔/poster.png"]),
        }
        paths = _build_image_paths_from_selection(fields, "小灯塔")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].name, "poster.png")

    def test_selected_hll_typed_image_resolves(self):
        fields = {
            "selected_project_images": json.dumps(
                ["画啦啦/types/17-封面/cover.jpg"]
            ),
        }
        paths = _build_image_paths_from_selection(fields, "画啦啦")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].name, "cover.jpg")


if __name__ == "__main__":
    unittest.main()
