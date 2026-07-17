import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

from multi_size_export import (
    build_edge_feather_mask,
    export_manual_splash_uploads,
    export_splash_subframe_sizes,
    build_manual_only_export_result,
    merge_multi_size_export_results,
    manual_upload_field_key,
    fit_contain_box,
    fit_image_contain_blur_extend,
    fit_image_contain_mirror_extend,
    fit_image_contain_seamless_extend,
    fit_image_cover_crop,
    overlay_preserved_center,
    overlay_preserved_center_feathered,
    render_splash_canvas,
)
from gpt_image_client import resolve_gpt_work_size
from ai_outpaint import (
    build_edge_reference_paths,
    build_outpaint_mask,
    compute_outpaint_gaps,
    finalize_outpaint_result,
    run_ai_extend_to_size,
    splash_extend_prompt,
    splash_subframe_extend_prompt,
)


class TestSplashFitModes(unittest.TestCase):
    def _sample(self, w=400, h=800):
        img = Image.new("RGBA", (w, h), (30, 120, 220, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        with Image.open(BytesIO(buf.getvalue())) as loaded:
            return loaded.convert("RGBA")

    def test_seamless_extend_preserves_center_and_matches_edges(self):
        src = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        for x in range(100):
            for y in range(100):
                src.putpixel((x, y), (50, 100, 200, 255))
        out = fit_image_contain_seamless_extend(src, 200, 200)
        self.assertEqual(out.size, (200, 200))
        self.assertEqual(out.getpixel((100, 100))[:3], (50, 100, 200))
        self.assertEqual(out.getpixel((10, 100))[:3], (50, 100, 200))
        self.assertEqual(out.getpixel((190, 100))[:3], (50, 100, 200))

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
        self.assertIn("左侧扩边", prompt)
        self.assertIn("Outpainting", prompt)
        self.assertIn("1080×1920", prompt)

    def test_splash_extend_prompt_includes_directional_gaps(self):
        gaps = {"left": 120, "right": 280, "top": 0, "bottom": 0}
        prompt = splash_extend_prompt(1080, 1920, gaps)
        self.assertIn("左侧需扩展约 120px", prompt)
        self.assertIn("右侧需扩展约 280px", prompt)
        self.assertIn("左右横向扩边", prompt)

    def test_mirror_extend_preserves_center_and_fills_sides(self):
        src = Image.new("RGBA", (100, 100), (80, 120, 160, 255))
        out = fit_image_contain_mirror_extend(src, 200, 100)
        self.assertEqual(out.size, (200, 100))
        self.assertEqual(out.getpixel((100, 50))[:3], (80, 120, 160))
        self.assertEqual(out.getpixel((10, 50))[:3], (80, 120, 160))

    def test_edge_reference_paths_prioritize_wider_gaps(self):
        fitted = Image.new("RGBA", (80, 160), (10, 20, 30, 255))
        gaps = {"left": 10, "right": 200, "top": 5, "bottom": 5}
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_edge_reference_paths(
                fitted,
                Path(tmpdir),
                gaps=gaps,
                max_refs=2,
            )
        self.assertEqual(len(paths), 2)
        names = " ".join(p.name for p in paths)
        self.assertIn("right", names)
        self.assertIn("left", names)

    def test_compute_outpaint_gaps(self):
        gaps = compute_outpaint_gaps(200, 200, 40, 20, 120, 160)
        self.assertEqual(gaps, {"left": 40, "right": 40, "top": 20, "bottom": 20})

    def test_render_splash_canvas_local_extend(self):
        src = self._sample()
        out = render_splash_canvas(src, 720, 1280)
        self.assertEqual(out.size, (720, 1280))

    def test_render_ai_failure_raises(self):
        src = self._sample()

        def fail_ai(_src, _w, _h):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            render_splash_canvas(src, 720, 1280, fit_mode="extend", ai_canvas_fn=fail_ai)

    def test_overlay_preserved_center_keeps_original_pixels(self):
        src = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
        ai_bg = Image.new("RGBA", (200, 200), (0, 255, 0, 255))
        out = overlay_preserved_center(ai_bg, src, 200, 200)
        self.assertEqual(out.getpixel((100, 100))[:3], (255, 0, 0))
        self.assertEqual(out.getpixel((10, 10))[:3], (0, 255, 0))

    def test_feathered_overlay_blends_border(self):
        edge = build_edge_feather_mask(100, 100, 24)
        self.assertGreater(edge.getpixel((50, 50)), 200)
        self.assertLess(edge.getpixel((0, 50)), 80)
        src = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        ai_bg = Image.new("RGBA", (200, 200), (0, 255, 0, 255))
        soft = overlay_preserved_center_feathered(ai_bg, src, 200, 200, feather_px=24)
        self.assertEqual(soft.getpixel((100, 100))[:3], (255, 0, 0))

    def test_gpt_work_size_matches_api_preset(self):
        w, h = resolve_gpt_work_size(1440, 2560)
        self.assertIn((w, h), ((1024, 1536), (1536, 1024)))

    def test_build_outpaint_mask_marks_center_opaque(self):
        mask = build_outpaint_mask(200, 200, 50, 50, 100, 100)
        self.assertEqual(mask.size, (200, 200))
        self.assertGreater(mask.getpixel((100, 100))[3], 200)
        self.assertLess(mask.getpixel((10, 10))[3], 10)

    def test_finalize_fills_target_without_black_letterbox(self):
        src = Image.new("RGBA", (400, 800), (30, 120, 220, 255))
        ai = Image.new("RGBA", (1024, 1536), (0, 0, 0, 0))
        out = finalize_outpaint_result(
            ai,
            src,
            1440,
            2560,
            work_w=1024,
            work_h=1536,
        )
        self.assertEqual(out.size, (1440, 2560))
        top = out.getpixel((720, 5))[:3]
        bottom = out.getpixel((720, 2555))[:3]
        self.assertNotEqual(top, (0, 0, 0))
        self.assertNotEqual(bottom, (0, 0, 0))
        self.assertEqual(out.getpixel((720, 1280))[:3], (30, 120, 220))

    def test_run_ai_extend_preserves_original_without_stretch(self):
        src = Image.new("RGBA", (80, 160), (255, 128, 0, 255))

        def fake_img2img(path: Path, prompt: str, ratio: str, mask_path=None, aw=0, ah=0, refs=None):
            return "file://fake", None

        def fake_download(url: str, dest: Path) -> None:
            # 模拟 GPT 返回错误宽高比（扁平图），若直接拉伸会压扁原图
            Image.new("RGBA", (320, 80), (0, 0, 255, 255)).save(dest, format="PNG")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = run_ai_extend_to_size(
                src,
                200,
                200,
                prompt=splash_extend_prompt(200, 200),
                ratio="1:1",
                upload_dir=Path(tmpdir),
                img2img=fake_img2img,
                download_image=fake_download,
            )
        fitted, ox, oy = fit_contain_box(src, 200, 200)
        cx = ox + fitted.width // 2
        cy = oy + fitted.height // 2
        self.assertEqual(out.size, (200, 200))
        self.assertEqual(out.getpixel((cx, cy))[:3], (255, 128, 0))
        self.assertEqual(fitted.size, (100, 200))

    def test_run_ai_extend_fails_without_local_fallback(self):
        src = Image.new("RGBA", (80, 80), (255, 128, 0, 255))

        def fail_img2img(path: Path, prompt: str, ratio: str, mask_path=None, aw=0, ah=0, refs=None):
            return None, "api down"

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                run_ai_extend_to_size(
                    src,
                    200,
                    200,
                    prompt=splash_extend_prompt(200, 200),
                    ratio="1:1",
                    upload_dir=Path(tmpdir),
                    img2img=fail_img2img,
                    download_image=lambda _u, _d: None,
                )


class TestSplashSubframePrompt(unittest.TestCase):
    def test_splash_subframe_prompt_contains_core_rules_and_size(self):
        prompt = splash_subframe_extend_prompt(1125, 2436)
        self.assertIn("根据生成的尺寸要求，以参考图为视觉基准扩展上下左右的画面", prompt)
        self.assertIn("不要去掉底部色块", prompt)
        self.assertIn("不要出现拼接缝", prompt)
        self.assertIn("1125×2436", prompt)

    def test_splash_subframe_prompt_appends_remark(self):
        prompt = splash_subframe_extend_prompt(1125, 2436, remark="保留顶部留白，底部色块不要裁切")
        self.assertIn("保留顶部留白，底部色块不要裁切", prompt)
        self.assertIn("1125×2436", prompt)

    def test_splash_subframe_prompt_ignores_blank_remark(self):
        base = splash_subframe_extend_prompt(1125, 2436)
        self.assertEqual(base, splash_subframe_extend_prompt(1125, 2436, remark="   "))

    def test_splash_subframe_prompt_400x400_fixed_vertical_layout(self):
        prompt = splash_subframe_extend_prompt(400, 400)
        self.assertIn("400×400", prompt)
        self.assertIn("顶部是标题", prompt)
        self.assertIn("底部是角色", prompt)
        self.assertIn("上下排版", prompt)
        # 横版尺寸不注入上下布局
        other = splash_subframe_extend_prompt(750, 280)
        self.assertNotIn("顶部是标题", other)

    def test_splash_subframe_prompt_horizontal_sizes_fixed_layout(self):
        for w, h in ((690, 320), (750, 280), (750, 422)):
            prompt = splash_subframe_extend_prompt(w, h)
            self.assertIn(f"{w}×{h}", prompt)
            self.assertIn("左边是标题", prompt)
            self.assertIn("右边是角色", prompt)
            self.assertIn("左右排版", prompt)
        square = splash_subframe_extend_prompt(400, 400)
        self.assertNotIn("左边是标题", square)


class TestSplashSubframeExport(unittest.TestCase):
    def test_skips_ai_for_hero_size_when_source_is_hero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "src.png"
            output_dir = tmp_path / "outputs"
            Image.new("RGB", (1440, 2560), color=(10, 120, 200)).save(input_path)
            calls: list[tuple[int, int]] = []

            def fake_gen(_src, tw, th):
                calls.append((tw, th))
                return Image.new("RGBA", (tw, th), color=(255, 0, 0, 255))

            sizes = [
                {"id": "expand_1440x2560", "name": "1440×2560", "width": 1440, "height": 2560},
                {"id": "expand_1125x2436", "name": "1125×2436", "width": 1125, "height": 2436},
            ]
            result = export_splash_subframe_sizes(
                input_path,
                output_dir,
                "job_hero",
                sizes=sizes,
                generate_at_size=fake_gen,
                make_zip=False,
            )
            self.assertEqual(calls, [(1125, 2436)])
            self.assertEqual(len(result["images"]), 2)
            hero = next(item for item in result["images"] if item["width"] == 1440)
            self.assertEqual(hero["source"], "passthrough")
            ai_item = next(item for item in result["images"] if item["width"] == 1125)
            self.assertEqual(ai_item["source"], "ai")

    def test_expands_all_sizes_when_source_not_hero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "src.png"
            output_dir = tmp_path / "outputs"
            Image.new("RGB", (1125, 2436), color=(10, 120, 200)).save(input_path)
            calls: list[tuple[int, int]] = []

            def fake_gen(_src, tw, th):
                calls.append((tw, th))
                return Image.new("RGBA", (tw, th), color=(255, 0, 0, 255))

            sizes = [
                {"id": "expand_1440x2560", "name": "1440×2560", "width": 1440, "height": 2560},
                {"id": "expand_1125x2436", "name": "1125×2436", "width": 1125, "height": 2436},
            ]
            export_splash_subframe_sizes(
                input_path,
                output_dir,
                "job_all",
                sizes=sizes,
                generate_at_size=fake_gen,
                make_zip=False,
            )
            self.assertEqual(calls, [(1440, 2560), (1125, 2436)])


class TestSplashManualUploads(unittest.TestCase):
    def test_export_manual_splash_sources_to_five_target_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp) / "uploads"
            output_dir = Path(tmp) / "outputs"
            upload_dir.mkdir()
            output_dir.mkdir()
            fields = {}
            for w, h, color in (
                (1440, 2560, (10, 120, 200)),
                (1125, 2436, (20, 130, 210)),
                (1536, 2048, (30, 140, 220)),
                (1668, 2388, (40, 150, 230)),
            ):
                buf = BytesIO()
                Image.new("RGB", (w, h), color=color).save(buf, format="PNG")
                fields[manual_upload_field_key(w, h)] = {
                    "filename": f"{w}x{h}.png",
                    "data": buf.getvalue(),
                }
            outputs, source_outputs = export_manual_splash_uploads(
                fields,
                upload_dir,
                output_dir,
                "job1",
                source_basename="测试开屏",
            )
            self.assertEqual(len(outputs), 5)
            self.assertEqual(len(source_outputs), 4)
            expected = {(480, 854), (720, 1280), (1080, 1920), (1080, 2340), (1242, 2208)}
            got = {(item["width"], item["height"]) for item in outputs}
            self.assertEqual(got, expected)
            for item in outputs:
                out_path = output_dir / item["filename"]
                self.assertTrue(out_path.is_file())
                with Image.open(out_path) as im:
                    self.assertEqual(im.size, (item["width"], item["height"]))

    def test_manual_upload_rejects_wrong_pixel_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp) / "uploads"
            output_dir = Path(tmp) / "outputs"
            upload_dir.mkdir()
            output_dir.mkdir()
            buf = BytesIO()
            Image.new("RGB", (800, 600), color=(10, 120, 200)).save(buf, format="PNG")
            fields = {
                manual_upload_field_key(1440, 2560): {
                    "filename": "wrong.png",
                    "data": buf.getvalue(),
                }
            }
            with self.assertRaises(ValueError) as ctx:
                export_manual_splash_uploads(
                    fields,
                    upload_dir,
                    output_dir,
                    "job_wrong",
                    source_basename="测试开屏",
                )
            self.assertIn("上传尺寸错误", str(ctx.exception))

    def test_build_manual_only_export_result_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            outputs = [{
                "id": "s480x854",
                "name": "480×854",
                "width": 480,
                "height": 854,
                "filename": "multi_job2_s480x854.jpg",
                "downloadName": "开屏_480x854.jpg",
                "url": "/outputs/multi_job2_s480x854.jpg",
                "fileSize": 100,
            }]
            sources = [{
                "filename": "multi_job2_manual_1440x2560_src.jpg",
                "downloadName": "开屏_1440x2560.jpg",
            }]
            img_path = output_dir / outputs[0]["filename"]
            Image.new("RGB", (480, 854), color=(255, 0, 0)).save(img_path, quality=90)
            src_path = output_dir / sources[0]["filename"]
            Image.new("RGB", (1440, 2560), color=(0, 255, 0)).save(src_path, quality=90)
            result = build_manual_only_export_result(
                outputs,
                output_dir,
                "job2",
                source_basename="开屏",
                source_outputs=sources,
            )
            self.assertEqual(result["count"], 1)
            zip_path = output_dir / result["zip_filename"]
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
            self.assertEqual(names, {"开屏_480x854.jpg", "开屏_1440x2560.jpg"})


if __name__ == "__main__":
    unittest.main()
