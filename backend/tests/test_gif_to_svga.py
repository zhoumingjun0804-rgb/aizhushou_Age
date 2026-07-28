import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gif_to_svga.converter import (
    fps_preserving_duration,
    gif_to_svga,
    validate_svga_file,
)


class TestGifToSvgaTiming(unittest.TestCase):
    def test_fps_preserving_duration_halves_when_frames_halved(self):
        # 48 帧、4 秒 → 约 12 FPS；抽成 24 帧应约 6 FPS，总时长仍约 4 秒
        self.assertEqual(fps_preserving_duration(48, 4000), 12)
        self.assertEqual(fps_preserving_duration(24, 4000), 6)

    def test_thin_frames_keeps_playback_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            gif_path = tmp / "slow.gif"
            out_path = tmp / "out.svga"
            frames = [
                Image.new("RGBA", (80, 80), (i * 20, 40, 200 - i * 10, 255))
                for i in range(24)
            ]
            # 每帧 100ms → 总时长 2.4s；体积偏大时会抽帧
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=[100] * 24,
                loop=0,
                disposal=2,
            )
            result = gif_to_svga(gif_path, out_path, max_bytes=8 * 1024)
            self.assertTrue(out_path.is_file())
            meta = validate_svga_file(out_path)
            gif_sec = 2.4
            svga_sec = meta["totalFrames"] / float(meta["fps"])
            # 允许离散合法 FPS 带来的小误差
            self.assertLess(abs(svga_sec - gif_sec), 0.55)
            if result.get("framesReduced"):
                self.assertLess(meta["fps"], 12)

    def test_svga_compatible_with_mobile_player_conventions(self):
        import io
        import zlib

        from gif_to_svga.proto import svga_pb2

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            gif_path = tmp / "tiny.gif"
            out_path = tmp / "out.svga"
            frames = [
                Image.new("RGBA", (40, 20), (i * 40, 80, 120, 255)) for i in range(4)
            ]
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=[80] * 4,
                loop=0,
                disposal=2,
            )
            gif_to_svga(gif_path, out_path, max_bytes=0)
            movie = svga_pb2.MovieEntity()
            movie.ParseFromString(zlib.decompress(out_path.read_bytes()))
            self.assertTrue(movie.version.startswith("2.1"))
            self.assertTrue(movie.images)
            self.assertTrue(all("." not in k for k in movie.images.keys()))
            for sp in movie.sprites:
                for fr in sp.frames:
                    self.assertGreater(fr.layout.width, 0)
                    self.assertGreater(fr.layout.height, 0)
                    self.assertEqual(fr.transform.a, 1.0)
                    self.assertEqual(fr.transform.d, 1.0)
            # 资源图应为 RGBA PNG，而非索引色
            sample = next(iter(movie.images.values()))
            im = Image.open(io.BytesIO(sample))
            self.assertEqual(im.mode, "RGBA")


if __name__ == "__main__":
    unittest.main()
