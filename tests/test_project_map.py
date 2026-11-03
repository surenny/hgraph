from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from hgraph.dashboard import hydrate_project_data, project_data, project_source
from hgraph.graph import Graph, HGraphError
from hgraph.server import _full_payload, _project_parts
from hgraph.site import (_content_tabs, _copy_webui, _map_config, write_solo,
                         write_static_site)


class MapConfigTests(unittest.TestCase):
    def test_optional_map_normalization(self):
        for disabled in (None, False, {"enabled": False}, {"enabled": False, "label": "Map"}):
            with self.subTest(disabled=disabled):
                self.assertIsNone(_map_config(disabled))
        default = {"enabled": True, "label": "Blueprint map"}
        for enabled in (True, {}, {"enabled": True}):
            with self.subTest(enabled=enabled):
                self.assertEqual(_map_config(enabled), default)
        self.assertEqual(_map_config({"label": "  Argument map  "}),
                         {"enabled": True, "label": "Argument map"})

    def test_invalid_map_options_raise(self):
        for invalid in ("true", 0, 1, [], {"enabled": "false"}, {"enabled": 1},
                        {"label": None}, {"label": " "}, {"label": 5},
                        {"enabled": False, "label": False}, {"enable": True}):
            with self.subTest(invalid=invalid), self.assertRaises(HGraphError):
                _map_config(invalid)


class TabPresentationTests(unittest.TestCase):
    def test_page_output_is_compatible_and_modal_html_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = '<p data-action="close">Original HTML and $x=x$.</p>'
            (root / "content.html").write_text(html, encoding="utf-8")
            original = {"id": "notes", "label": "Notes", "content": "content.html"}
            default = _content_tabs([original], base=root, where="project")
            page = _content_tabs([{**original, "presentation": "page"}], base=root, where="project")
            self.assertEqual(default, [{"id": "notes", "label": "Notes", "html": html}])
            self.assertEqual(page, default)
            modal = _content_tabs([{**original, "presentation": "modal"}], base=root, where="project")
            self.assertEqual(modal, [{**default[0], "presentation": "modal"}])

    def test_disabled_tab_needs_no_content_and_does_not_warn(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(_content_tabs([
                {"id": "hidden", "enabled": False},
                {"id": "missing", "enabled": False, "label": "Missing", "content": "not-there.html"},
            ], base=Path("."), where="project"), [])
        self.assertEqual(output.getvalue(), "")

    def test_invalid_presentation_or_enabled_values_are_rejected_even_when_hidden(self):
        for presentation in (None, True, "popup", "MODAL", [], {}):
            for enabled in (True, False):
                with self.subTest(presentation=presentation, enabled=enabled), self.assertRaisesRegex(HGraphError, "presentation"):
                    _content_tabs([{"id": "notes", "enabled": enabled, "presentation": presentation}],
                                  base=Path("."), where="project")
        for enabled in (None, 0, 1, "false"):
            with self.subTest(enabled=enabled), self.assertRaisesRegex(HGraphError, "enabled"):
                _content_tabs([{"id": "notes", "enabled": enabled}], base=Path("."), where="project")

    def test_reserved_and_duplicate_ids_fail_before_reading_any_content(self):
        for where in ("project", "manifest"):
            for reserved in ("projects", "overview", "summary", "graph", "doc", "biblio", "map"):
                for enabled in (True, False):
                    with self.subTest(where=where, reserved=reserved, enabled=enabled), self.assertRaisesRegex(HGraphError, "reserved"):
                        _content_tabs([{"id": reserved, "enabled": enabled}], base=Path("."), where=where)
            with self.assertRaisesRegex(HGraphError, "duplicate"):
                _content_tabs([{"id": "notes", "enabled": False},
                               {"id": " notes ", "enabled": False}], base=Path("."), where=where)


class ProjectMapPayloadTests(unittest.TestCase):
    def make_project(self, root: Path, site: dict) -> Graph:
        (root / "hgraph").mkdir(parents=True)
        (root / "hgraph/config.yaml").write_text(yaml.safe_dump({"site": site}), encoding="utf-8")
        return Graph.open(root)

    def test_full_lazy_and_live_payloads_have_identical_optional_project_options(self):
        for site in ({}, {"map": False}, {"map": {"enabled": False}},
                     {"map": True}, {"map": {"enabled": True, "label": "Argument map"}},
                     {"graph_progress": "local"}, {"map": True, "graph_progress": "closure"}):
            with self.subTest(site=site), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                graph = self.make_project(root, site)
                full = project_data(graph, title="Project", root=str(root), include_layout=False)
                source = project_source(graph, title="Project", root=str(root))
                parts = _project_parts(graph, title="Project", root=str(root))
                with patch("hgraph.layout.render_svgs", return_value={}):
                    payloads = [full, source["data"], source["shell"],
                                hydrate_project_data(source, graph), json.loads(parts["shell"]),
                                json.loads(_full_payload(parts, graph))]
                expected = _map_config(site.get("map"))
                for payload in payloads:
                    if expected is None:
                        self.assertNotIn("map", payload)
                    else:
                        self.assertEqual(payload["map"], expected)
                    self.assertEqual(payload["customTabs"], [])
                    self.assertEqual(payload.get("graphProgress"), site.get("graph_progress"))

    def test_workspace_and_solo_static_exports_keep_map_and_modal_config(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "project"
            site = {"map": {"enabled": True, "label": "Argument map"}, "graph_progress": "local", "tabs": [
                {"id": "notes", "label": "Notes", "content": "notes.html", "presentation": "modal"},
                {"id": "hidden", "enabled": False},
            ]}
            self.make_project(project, site)
            (project / "notes.html").write_text("<p>Keep this HTML.</p>", encoding="utf-8")
            expected_tabs = [{"id": "notes", "label": "Notes", "html": "<p>Keep this HTML.</p>",
                              "presentation": "modal"}]
            graph = Graph.open(project)
            parts = _project_parts(graph, title="Project", root=str(project))
            self.assertEqual(json.loads(parts["shell"])["customTabs"], expected_tabs)
            with patch("hgraph.layout.render_svgs", return_value={}):
                manifest = {"title": "Workspace", "projects": [{"name": "Project", "root": "project"}]}
                write_static_site(manifest, base=workspace, out_path=workspace / "workspace-site/index.html")
                write_solo(site, root=project, out_path=workspace / "solo-site/index.html")
            for export in (workspace / "workspace-site/project", workspace / "solo-site"):
                for filename in ("data.json", "project.json"):
                    payload = json.loads((export / filename).read_text(encoding="utf-8"))
                    self.assertEqual(payload["map"], {"enabled": True, "label": "Argument map"})
                    self.assertEqual(payload["customTabs"], expected_tabs)
                    self.assertEqual(payload["graphProgress"], "local")

    def test_invalid_project_options_are_rejected_by_both_payload_paths(self):
        for invalid in ({"map": "true"}, {"tabs": [{"id": "map", "enabled": False}]},
                        {"tabs": [{"id": "notes", "presentation": "popup", "enabled": False}]}):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                graph = self.make_project(root, invalid)
                with self.assertRaises(HGraphError):
                    project_data(graph, title="Project", root=str(root), include_layout=False)
                with self.assertRaises(HGraphError):
                    project_source(graph, title="Project", root=str(root))

    def test_static_copy_includes_builtin_renderer_and_custom_page_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, output = root / "bundle", root / "output"
            (bundle / "blueprint-map").mkdir(parents=True)
            (output / "assets").mkdir(parents=True)
            files = {"hgraph-custom-page.js": "window.hgraphCustomPage = {};",
                     "blueprint-map/renderer.html": "<html>Built-in map</html>",
                     "blueprint-map/renderer.js": "/* map data comes from ProjectData */"}
            for name, content in files.items():
                (bundle / name).write_text(content, encoding="utf-8")
            (bundle / "index.html").write_text("Injected separately", encoding="utf-8")
            (output / "assets/user-image.svg").write_text("Keep user asset", encoding="utf-8")
            with patch("hgraph.site.WEBUI_DIR", bundle):
                _copy_webui(output)
            for name, content in files.items():
                self.assertEqual((output / name).read_text(encoding="utf-8"), content)
            self.assertFalse((output / "index.html").exists())
            self.assertEqual((output / "assets/user-image.svg").read_text(encoding="utf-8"), "Keep user asset")


if __name__ == "__main__":
    unittest.main()
