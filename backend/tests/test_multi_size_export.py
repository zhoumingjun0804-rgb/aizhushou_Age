import unittest
from io import BytesIO

from PIL import Image

from multi_size_export import fit_image_contain_blur_extend, fit_image_cover_crop, render_splash_canvas
from ai_outpaint import splash_extend_prompt


class TestSplashFitModes(unittest.TestCase):
    def _sample(self, w=400, h=800):
        img = Image.new("RGBA", (w, h), (30, 120, 220, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        with Image.open(BytesIO(buf.getvalue())) as loaded:
            return loaded.convert("RGBA")

    def test_contain_blur_extend_keeps_full_canvas_size(self):
        src = self._sample(400, 800)
        out = fit_image_contain_blur_extend(src, 1080, 1920)
        self.assertEqual(out.size, (1080, 1920))

    def test_contain_blur_extend_does_not_crop_when_aspect_changes(self):
        src = self._sample(480, 854)
        out = fit_image_contain_blur_extend(src, 1080, 2340)
        self.assertEqual(out.size, (1080, 2340))
        # 中心像素应来自原图主体，而非纯背景色
        center = out.getpixel((540, 1170))
        self.assertNotEqual(center[:3], (0, 0, 0))

    def test_cover_crop_fills_canvas(self):
        src = self._sample(400, 800)
        out = fit_image_cover_crop(src, 1080, 1920)
        self.assertEqual(out.size, (1080, 1920))

    def test_splash_extend_prompt_forbids_content_change(self):
        prompt = splash_extend_prompt(1080, 1920)
        self.assertIn("禁止 img2img 重绘", prompt)
        self.assertIn("Outpainting", prompt)
        self.assertIn("1080x1920", prompt)

        src = self._sample()
        out = render_splash_canvas(src, 720, 1280)
        self.assertEqual(out.size, (720, 1280))

    def test_render_ai_failure_falls_back_to_extend(self):
        src = self._sample()

        def fail_ai(_src, _w, _h):
            raise RuntimeError("boom")

        out = render_splash_canvas(src, 720, 1280, fit_mode="extend", ai_canvas_fn=fail_ai)
        self.assertEqual(out.size, (720, 1280))


if __name__ == "__main__":
    unittest.main()
