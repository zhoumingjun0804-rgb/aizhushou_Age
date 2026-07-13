import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_cutout import (
    _build_local_subject_alpha,
    _extract_subject_cutout_local,
    _fill_foreground_holes,
    _mask_image_to_foreground,
)


class TestImageCutout(unittest.TestCase):
    def test_local_cutout_preserves_internal_white(self):
        """主体内部的白色区域不应被当作背景抠掉。"""
        im = Image.new("RGBA", (120, 120), (245, 238, 220, 255))
        px = im.load()
        for x in range(30, 90):
            for y in range(30, 90):
                px[x, y] = (40, 80, 200, 255)
        for x in range(50, 70):
            for y in range(50, 70):
                px[x, y] = (255, 255, 255, 255)

        border = 3
        palette_samples = []
        for y in range(120):
            for x in range(120):
                if x < border or x >= 117 or y < border or y >= 117:
                    r, g, b, a = px[x, y]
                    if a > 0:
                        palette_samples.append((r, g, b))
        from collections import Counter

        bins = Counter((r // 16, g // 16, b // 16) for r, g, b in palette_samples)
        palette = [(r * 16 + 8, g * 16 + 8, b * 16 + 8) for (r, g, b), _ in bins.most_common(6)]

        alpha = _build_local_subject_alpha(im, palette, 60, 60, roi_x=30, roi_y=30, roi_w=60, roi_h=60)
        center = alpha.getpixel((60, 60))
        corner = alpha.getpixel((5, 5))
        self.assertGreater(center, 200, "内部白色应保留为不透明")
        self.assertLess(corner, 20, "边缘背景应被去除")

    def test_local_cutout_preserves_internal_dark_eyes(self):
        """主体内部的深色细节（如眼睛）应保留。"""
        im = Image.new("RGBA", (120, 120), (245, 238, 220, 255))
        px = im.load()
        for x in range(30, 90):
            for y in range(30, 90):
                px[x, y] = (20, 20, 20, 255)
        for x in range(45, 75):
            for y in range(45, 75):
                px[x, y] = (255, 255, 255, 255)
        for x in range(52, 58):
            for y in range(52, 58):
                px[x, y] = (0, 0, 0, 255)
        for x in range(62, 68):
            for y in range(52, 58):
                px[x, y] = (0, 0, 0, 255)

        border = 3
        palette_samples = []
        for y in range(120):
            for x in range(120):
                if x < border or x >= 117 or y < border or y >= 117:
                    r, g, b, a = px[x, y]
                    if a > 0:
                        palette_samples.append((r, g, b))
        from collections import Counter

        bins = Counter((r // 16, g // 16, b // 16) for r, g, b in palette_samples)
        palette = [(r * 16 + 8, g * 16 + 8, b * 16 + 8) for (r, g, b), _ in bins.most_common(6)]

        alpha = _build_local_subject_alpha(im, palette, 60, 60, roi_x=30, roi_y=30, roi_w=60, roi_h=60)
        self.assertGreater(alpha.getpixel((55, 55)), 200, "左眼应保留")
        self.assertGreater(alpha.getpixel((65, 55)), 200, "右眼应保留")

    def test_fill_foreground_holes(self):
        fg, w, h = _mask_image_to_foreground(Image.new("L", (5, 5), 0))
        fg[0] = 1
        fg[1] = 1
        fg[2] = 1
        fg[3] = 1
        fg[4] = 1
        fg[5] = 1
        fg[9] = 1
        fg[10] = 1
        fg[11] = 1
        fg[12] = 1
        fg[13] = 1
        fg[14] = 1
        fg[15] = 1
        fg[19] = 1
        fg[20] = 1
        fg[21] = 1
        fg[22] = 1
        fg[23] = 1
        fg[24] = 1
        _fill_foreground_holes(fg, w, h)
        self.assertEqual(fg[12], 1, "前景轮廓内的孔洞应被填补")

    def test_local_cutout_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "src.png"
            out = tmp / "out.png"
            im = Image.new("RGBA", (100, 100), (250, 245, 235, 255))
            px = im.load()
            for x in range(25, 75):
                for y in range(25, 75):
                    px[x, y] = (200, 50, 50, 255)
            for x in range(40, 60):
                for y in range(40, 60):
                    px[x, y] = (255, 255, 255, 255)
            for x in range(46, 49):
                for y in range(46, 49):
                    px[x, y] = (0, 0, 0, 255)
            im.save(src)

            w, h = _extract_subject_cutout_local(
                src,
                out,
                trim=False,
                roi_cx=50,
                roi_cy=50,
                roi_x=25,
                roi_y=25,
                roi_w=50,
                roi_h=50,
            )
            self.assertEqual((w, h), (100, 100))
            result = Image.open(out).convert("RGBA")
            self.assertGreater(result.getpixel((50, 50))[3], 200)
            self.assertGreater(result.getpixel((47, 47))[3], 200, "内部深色细节应保留")
            self.assertLess(result.getpixel((2, 2))[3], 20)


if __name__ == "__main__":
    unittest.main()
