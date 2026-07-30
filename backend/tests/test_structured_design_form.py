import re
import unittest
from pathlib import Path


INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def _find_matching_brace(source, opening_index):
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
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1

    raise AssertionError("Unbalanced JavaScript function body")


def _extract_js_function(source, name):
    declaration = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    if declaration is None:
        raise AssertionError(f"JavaScript function {name} was not found")
    opening_brace = source.find("{", declaration.start())
    closing_brace = _find_matching_brace(source, opening_brace)
    return source[opening_brace + 1 : closing_brace]


def _element_with_id(source, element_id):
    return re.search(
        rf"<(?P<tag>[a-zA-Z][\w:-]*)\b[^>]*\bid\s*=\s*"
        rf"(?P<quote>[\"']){re.escape(element_id)}(?P=quote)[^>]*>",
        source,
    )


def _assert_reads_id(test_case, body, element_id):
    test_case.assertRegex(
        body,
        rf"""document\.getElementById\(\s*(["']){re.escape(element_id)}\1\s*\)""",
    )


def _assigned_id_variable(source, element_id):
    assignment = re.search(
        rf"""(?:var|let|const)\s+(?P<variable>\w+)\s*=\s*"""
        rf"""document\.getElementById\(\s*(["']){re.escape(element_id)}\2\s*\)""",
        source,
    )
    if assignment is None:
        raise AssertionError(f"No variable reads #{element_id}")
    return assignment.group("variable")


def _assigned_lookup_variable(source, id_variable):
    assignment = re.search(
        rf"""(?:var|let|const)\s+(?P<variable>\w+)\s*=\s*"""
        rf"""document\.getElementById\(\s*{re.escape(id_variable)}\s*\)""",
        source,
    )
    if assignment is None:
        raise AssertionError(f"No element lookup uses loop variable {id_variable}")
    return assignment.group("variable")


def _extract_string_array_for_each(source):
    pattern = re.compile(
        r"""\[(?P<items>[^\]]*)\]\s*\.forEach\s*\(\s*"""
        r"""function\s*\(\s*(?P<parameter>\w+)\s*\)\s*\{"""
    )
    blocks = []
    for match in pattern.finditer(source):
        items = [
            value
            for _, value in re.findall(r"""(["'])(.*?)\1""", match.group("items"))
        ]
        opening_brace = source.find("{", match.start())
        closing_brace = _find_matching_brace(source, opening_brace)
        blocks.append(
            (items, match.group("parameter"), source[opening_brace + 1 : closing_brace])
        )
    return blocks


def _find_string_array_for_each(source, expected_items):
    blocks = _extract_string_array_for_each(source)
    if not blocks:
        raise AssertionError("No string-array forEach block was found")
    expected = set(expected_items)
    return max(blocks, key=lambda block: len(expected.intersection(block[0])))


def _extract_listener_callback(source, element_variable, event_variable):
    listener = re.search(
        rf"""\b{re.escape(element_variable)}\.addEventListener\(\s*"""
        rf"""{re.escape(event_variable)}\s*,\s*function\s*\([^)]*\)\s*\{{""",
        source,
    )
    if listener is None:
        raise AssertionError(
            f"No addEventListener callback binds {event_variable} on {element_variable}"
        )
    opening_brace = source.find("{", listener.start())
    closing_brace = _find_matching_brace(source, opening_brace)
    return source[opening_brace + 1 : closing_brace]


class StructuredDesignFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_structured_fields_replace_design_brief(self):
        for element_id in (
            "requirementName",
            "mainTitle",
            "subTitle",
            "visualDesc",
            "styleSelect",
            "customStyle",
            "layoutRef",
            "extraNotes",
        ):
            with self.subTest(element_id=element_id):
                self.assertIsNotNone(
                    _element_with_id(self.html, element_id),
                    f"HTML control #{element_id} was not found",
                )
        self.assertIsNone(
            _element_with_id(self.html, "designBrief"),
            "Legacy #designBrief must be removed",
        )

    def test_style_select_supports_custom_style(self):
        self.assertIsNotNone(
            _element_with_id(self.html, "customStyleInput"),
            "Custom style wrapper #customStyleInput was not found",
        )
        select = re.search(
            r"""<select\b(?=[^>]*\bid\s*=\s*(["'])styleSelect\1)[^>]*>"""
            r"""(?P<options>.*?)</select\s*>""",
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(select, "styleSelect was not found")
        opening_tag = select.group(0).split(">", 1)[0] + ">"
        self.assertRegex(
            opening_tag,
            r"""\bonchange\s*=\s*(["'])\s*toggleCustomStyle\(\)\s*\1""",
        )
        self.assertRegex(
            select.group("options"),
            r"""<option\b[^>]*\bvalue\s*=\s*(["'])custom\1[^>]*>""",
        )

        toggle_body = _extract_js_function(self.html, "toggleCustomStyle")
        style_select = _assigned_id_variable(toggle_body, "styleSelect")
        custom_input = _assigned_id_variable(toggle_body, "customStyleInput")
        self.assertRegex(
            toggle_body,
            rf"""\b{re.escape(style_select)}\.value\s*={{2,3}}\s*(["'])custom\1""",
        )
        self.assertRegex(
            toggle_body,
            rf"""\b{re.escape(custom_input)}\.style\.display\s*=\s*"""
            rf"""{re.escape(style_select)}\.value\s*={{2,3}}\s*(["'])custom\1"""
            r"""\s*\?\s*(["'])block\2\s*:\s*(["'])none\3""",
        )

        value_body = _extract_js_function(self.html, "getStyleValue")
        style_select = _assigned_id_variable(value_body, "styleSelect")
        custom_style = _assigned_id_variable(value_body, "customStyle")
        self.assertRegex(
            value_body,
            rf"""\b{re.escape(style_select)}\.value\s*={{2,3}}\s*(["'])custom\1""",
        )
        self.assertRegex(
            value_body,
            rf"""return\s+{re.escape(custom_style)}\s*\?\s*"""
            rf"""{re.escape(custom_style)}\.value\.trim\(\)\s*:\s*(["'])\1""",
        )
        self.assertRegex(
            value_body,
            rf"""return\s+{re.escape(style_select)}\.value\s*;""",
        )

    def test_build_design_summary_reads_structured_fields(self):
        body = _extract_js_function(self.html, "buildDesignSummary")
        mappings = {
            "主标题": "mainTitle",
            "副标题": "subTitle",
            "画面描述": "visualDesc",
            "排版参考": "layoutRef",
            "补充备注": "extraNotes",
        }
        for summary_key, element_id in mappings.items():
            with self.subTest(summary_key=summary_key):
                self.assertRegex(
                    body,
                    rf"""(["']){re.escape(summary_key)}\1\s*:\s*"""
                    rf"""document\.getElementById\(\s*(["'])"""
                    rf"""{re.escape(element_id)}\2\s*\)\.value\.trim\(\)""",
                )
        self.assertRegex(
            body,
            r"""(["'])风格\1\s*:\s*getStyleValue\s*\(\s*\)""",
        )
        self.assertNotRegex(body, r"\bparseDesignBrief\s*\(")

    def test_get_requirement_name_reads_its_field(self):
        body = _extract_js_function(self.html, "getRequirementName")
        _assert_reads_id(self, body, "requirementName")
        self.assertNotRegex(body, r"\bparseDesignBrief\s*\(")

    def test_form_change_reset_binds_all_structured_fields(self):
        body = _extract_js_function(self.html, "bindDesignFormChangeReset")
        expected_ids = [
            "mainTitle",
            "subTitle",
            "visualDesc",
            "layoutRef",
            "extraNotes",
            "styleSelect",
            "customStyle",
        ]
        bound_ids, id_variable, binding_body = _find_string_array_for_each(
            body, expected_ids
        )
        self.assertEqual(bound_ids, expected_ids)

        element_variable = _assigned_lookup_variable(binding_body, id_variable)
        event_names, event_variable, event_binding_body = (
            _find_string_array_for_each(binding_body, ["input", "change"])
        )
        self.assertEqual(event_names, ["input", "change"])
        listener_body = _extract_listener_callback(
            event_binding_body, element_variable, event_variable
        )
        self.assertRegex(listener_body, r"\bresetKeywordAnalysis\s*\(\s*\)")

    def test_reset_all_clears_all_structured_fields(self):
        body = _extract_js_function(self.html, "resetAll")
        expected_cleared_ids = [
            "requirementName",
            "mainTitle",
            "subTitle",
            "visualDesc",
            "layoutRef",
            "extraNotes",
            "customStyle",
        ]
        cleared_ids, id_variable, clear_body = _find_string_array_for_each(
            body, expected_cleared_ids
        )
        self.assertEqual(cleared_ids, expected_cleared_ids)
        element_variable = _assigned_lookup_variable(clear_body, id_variable)
        self.assertRegex(
            clear_body,
            rf"""\b{re.escape(element_variable)}\.value\s*=\s*(["'])\1""",
        )

        style_select = _assigned_id_variable(body, "styleSelect")
        self.assertRegex(
            body,
            rf"""\b{re.escape(style_select)}\.value\s*=\s*(["'])\1""",
        )
        self.assertRegex(body, r"\btoggleCustomStyle\s*\(\s*\)")
        self.assertNotRegex(body, r"""(["'])designBrief\1""")


if __name__ == "__main__":
    unittest.main()
