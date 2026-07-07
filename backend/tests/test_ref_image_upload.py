import io
import unittest
from pathlib import Path

from PIL import Image

from app import (
    GENERATION_REF_PROMPT_SUFFIX,
    _normalize_reference_upload,
    _save_ref_images_from_fields,
    build_generation_payload,
)


class TestRefImageUpload(unittest.TestCase):
    def test_save_ref_images_from_multipart_fields(self):
        png = io.BytesIO()
        Image.new("RGB", (4, 4), color="red").save(png, format="PNG")
        fields = {
            "ref_image_0": {
                "filename": "ref.png",
                "data": png.getvalue(),
            }
        }
        paths = _save_ref_images_from_fields(fields)
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_file())
        self.assertEqual(paths[0].suffix.lower(), ".png")

    def test_gif_reference_converted_to_png(self):
        gif = io.BytesIO()
        Image.new("RGB", (8, 8), color="blue").save(gif, format="GIF")
        gif_path = Path("backend/tests/_tmp_ref.gif")
        gif_path.write_bytes(gif.getvalue())
        try:
            out = _normalize_reference_upload(gif_path)
            self.assertEqual(out.suffix.lower(), ".png")
            self.assertTrue(out.is_file())
            self.assertFalse(gif_path.exists())
        finally:
            if gif_path.exists():
                gif_path.unlink()
            if out.exists():
                out.unlink()

    def test_build_generation_payload_adds_style_suffix_for_user_refs(self):
        png = io.BytesIO()
        Image.new("RGB", (4, 4), color="green").save(png, format="PNG")
        fields = {
            "project": "小灯塔",
            "count": "1",
            "client_id": "test",
            "image_backend": "gpt",
            "prompt": "测试标题",
            "kind": "with_prompt",
            "ref_image_0": {
                "filename": "ref.png",
                "data": png.getvalue(),
            },
        }
        payload = build_generation_payload(fields, "with_prompt")
        self.assertIn(GENERATION_REF_PROMPT_SUFFIX, payload["prompt"])
        self.assertTrue(payload["image_paths"])


if __name__ == "__main__":
    unittest.main()
