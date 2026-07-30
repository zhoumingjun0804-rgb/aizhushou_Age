import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def _find_matching_delimiter(source, opening_index, opening, closing):
    depth = 0
    quote = None
    escaped = False
    line_comment = False
    block_comment = False
    index = opening_index

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "/" and next_char == "/":
            line_comment = True
            index += 1
        elif char == "/" and next_char == "*":
            block_comment = True
            index += 1
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1

    raise AssertionError(f"Unbalanced {opening}{closing} block")


def _extract_js_function(source, name):
    declaration = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    if declaration is None:
        raise AssertionError(f"JavaScript function {name} was not found")
    opening_brace = source.find("{", declaration.start())
    closing_brace = _find_matching_delimiter(source, opening_brace, "{", "}")
    return source[opening_brace + 1 : closing_brace]


def _extract_if_blocks(source):
    for match in re.finditer(r"\bif\s*\(", source):
        opening_paren = source.find("(", match.start())
        closing_paren = _find_matching_delimiter(source, opening_paren, "(", ")")
        opening_brace = closing_paren + 1
        while opening_brace < len(source) and source[opening_brace].isspace():
            opening_brace += 1
        if opening_brace >= len(source) or source[opening_brace] != "{":
            continue
        closing_brace = _find_matching_delimiter(source, opening_brace, "{", "}")
        yield (
            source[opening_paren + 1 : closing_paren],
            source[opening_brace + 1 : closing_brace],
            match.start(),
        )


def _extract_catch_block(source):
    match = re.search(r"\bcatch\s*\([^)]*\)\s*\{", source)
    if match is None:
        raise AssertionError("JavaScript catch block was not found")
    opening_brace = source.find("{", match.start())
    closing_brace = _find_matching_delimiter(source, opening_brace, "{", "}")
    return source[opening_brace + 1 : closing_brace]


class ProjectRefsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_project_refs_section_not_feature_hidden(self):
        m = re.search(
            r'<div[^>]*id="projectRefsSection"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(m)
        self.assertNotIn("feature-hidden", m.group(0))

    def test_should_show_project_refs_not_hardcoded_false(self):
        body = _extract_js_function(self.html, "shouldShowProjectRefs")
        self.assertNotRegex(body, r"return\s+false\s*;")
        self.assertIn("getSelectedProjectName", body)

    def test_design_type_control_wires_existing_change_handler(self):
        select = re.search(r'<select[^>]*id="designType"[^>]*>', self.html)
        self.assertIsNotNone(select)
        tag = select.group(0)
        self.assertRegex(
            tag,
            r"""\bonchange\s*=\s*(["'])\s*onDesignTypeChange\(\)\s*\1""",
        )
        self.assertNotRegex(
            tag,
            r"""\bstyle\s*=\s*(["'])[^"']*display\s*:\s*none[^"']*\1""",
        )
        self.assertNotRegex(
            tag,
            r"""\baria-hidden\s*=\s*(["'])true\1""",
        )

        handler = _extract_js_function(self.html, "onDesignTypeChange")
        self.assertIn("onProjectOrDesignTypeChange", handler)

    def test_design_type_visibility_is_gated_by_folder_types(self):
        body = _extract_js_function(self.html, "updateDesignTypeVisibility")
        bar_assignment = re.search(
            r"""(?:var|let|const)\s+(?P<bar>\w+)\s*=\s*"""
            r"""document\.getElementById\(\s*(["'])designTypeBar\2\s*\)""",
            body,
        )
        self.assertIsNotNone(
            bar_assignment,
            "updateDesignTypeVisibility must target designTypeBar",
        )
        self.assertRegex(
            body,
            r"""document\.getElementById\(\s*(["'])designType\1\s*\)""",
        )

        visibility_assignment = re.search(
            r"""(?:var|let|const)\s+(?P<visible>\w+)\s*=\s*"""
            r"""(?P<condition>[^;]+folder_types[^;]+);""",
            body,
        )
        self.assertIsNotNone(
            visibility_assignment,
            "visibility condition must include folder_types",
        )
        condition = visibility_assignment.group("condition")
        self.assertRegex(condition, r"shouldShowProjectRefs\s*\(\)")
        self.assertRegex(
            condition,
            r"""currentProjectCatalog\s*={2,3}\s*(["'])folder_types\1""",
        )

        bar_name = re.escape(bar_assignment.group("bar"))
        visible_name = re.escape(visibility_assignment.group("visible"))
        self.assertRegex(
            body,
            rf"""\b{bar_name}\.style\.display\s*=\s*{visible_name}\s*\?"""
            r"""\s*(["'])flex\1\s*:\s*(["'])none\2""",
        )

    def test_folder_types_require_design_type_before_load(self):
        body = _extract_js_function(self.html, "selectProject")
        load_position = body.find("loadProjectImages")
        self.assertGreaterEqual(
            load_position,
            0,
            "selectProject must load project images after its guards",
        )

        gates = [
            (condition, block, position)
            for condition, block, position in _extract_if_blocks(body)
            if "folder_types" in condition
            and (
                re.search(r"!\s*designType\b", condition)
                or re.search(r"""designType\s*={2,3}\s*(["'])\1""", condition)
            )
        ]
        self.assertEqual(
            len(gates),
            1,
            "selectProject must have one folder_types + missing designType guard",
        )
        _, gate_body, gate_position = gates[0]
        self.assertLess(gate_position, load_position)
        self.assertIn("请选择设计类型", gate_body)
        self.assertRegex(gate_body, r"\breturn\s*;")
        self.assertNotIn("loadProjectImages", gate_body)

    def test_load_project_images_shows_user_facing_error(self):
        body = _extract_js_function(self.html, "loadProjectImages")
        catch_body = _extract_catch_block(body)
        self.assertRegex(
            catch_body,
            r"grid\.(?:innerHTML|textContent)\s*=\s*"
            r"[^;]*参考图加载失败，请刷新后重试[^;]*;",
        )

    def test_project_reference_hint_is_optional_and_left_aligned(self):
        body = _extract_js_function(self.html, "loadProjectImages")
        self.assertIn("项目参考图（可选，最多10张）", body)
        hint_style = re.search(
            r"\.project-images-grid\s+\.select-hint\s*\{(?P<body>[^}]*)\}",
            self.html,
        )
        self.assertIsNotNone(hint_style)
        self.assertRegex(hint_style.group("body"), r"text-align\s*:\s*left")


if __name__ == "__main__":
    unittest.main()
