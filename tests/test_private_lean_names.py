from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from hgraph.dashboard import collect_one
from hgraph.cli import main
from hgraph.graph import Graph, HGraphError, node_id
from hgraph.sync import parse_lean, sync


class PrivateLeanNamesTests(unittest.TestCase):
    def test_module_visibility_and_nested_sections(self):
        source = """/- module in a comment is not a command. -/
module
@[expose] section
namespace N
def hidden := 1
public def exported := 2
public noncomputable section
def sectionPublic := 3
namespace Inner
def nestedPublic := 4
private def explicitPrivate := 5
end Inner
end
def hiddenAgain := 6
end N
end
"""
        found = {d["fqname"]: d["private"] for d in parse_lean(source)}
        self.assertEqual(found, {
            "N.hidden": True, "N.exported": False, "N.sectionPublic": False,
            "N.Inner.nestedPublic": False, "N.Inner.explicitPrivate": True,
            "N.hiddenAgain": True,
        })

    def test_legacy_visibility_and_masked_module_text(self):
        source = '''/- module -/
def message := "module"
namespace N
theorem visible : True := by trivial
private theorem hidden : True := by trivial
end N
'''
        found = {d["fqname"]: d["private"] for d in parse_lean(source)}
        self.assertEqual(found, {"message": False, "N.visible": False, "N.hidden": True})

    def make_sources(self, parent, *, public=False):
        root = Path(parent)
        lean = root / "Lean"
        lean.mkdir()
        header = "" if public else "module\n"
        first, second = lean / "A.lean", lean / "B.lean"
        first.write_text(header + "namespace N\ndef helper : True := by trivial\nend N\n")
        second.write_text(header + "namespace N\ndef helper : True := by sorry\nend N\n")
        return root, first, second, Graph.open(root)

    def blueprint(self, root, target):
        path = root / "blueprint.tex"
        path.write_text(r"\begin{lemma}[Bound helper]" + "\n" + r"\label{bound}" + "\n" +
                        r"\lean{" + target + "}\n" + r"\end{lemma}" + "\n")
        return path

    def test_private_helpers_keep_independent_source_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory)
            report = sync(graph, lean_paths=[first, second], root=root)
            self.assertEqual(report["lean"], 2)
            self.assertEqual(report["warnings"], [])
            a = graph.get_node(graph.resolve("decl:Lean/A.lean::N.helper"))
            b = graph.get_node(graph.resolve("decl:Lean/B.lean::N.helper"))
            self.assertNotEqual(a.id, b.id)
            self.assertEqual(a.meta["lean_status"], "lean_ok")
            self.assertEqual(b.meta["lean_status"], "sorry")
            self.assertTrue(a.meta["private"])
            with self.assertRaisesRegex(HGraphError, "ambiguous"):
                graph.resolve("decl:N.helper")

    def test_ambiguous_binding_fails_before_changing_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory)
            bp = self.blueprint(root, "N.helper")
            with self.assertRaisesRegex(HGraphError, "ambiguous Lean binding") as error:
                sync(graph, blueprint=str(bp), lean_paths=[first, second], root=root)
            self.assertIn("Lean/A.lean::N.helper", str(error.exception))
            self.assertIn("Lean/B.lean::N.helper", str(error.exception))
            self.assertEqual(list(graph.nodes()), [])

    def test_cli_reports_ambiguous_binding_as_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _first, _second, graph = self.make_sources(directory)
            self.blueprint(root, "N.helper")
            graph.base.mkdir(parents=True)
            (graph.base / "config.yaml").write_text("blueprint: blueprint.tex\nlean: [Lean]\n")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["--root", str(root), "sync", "--color", "never"])
            self.assertNotEqual(code, 0)
            self.assertIn("ambiguous Lean binding", stdout.getvalue() + stderr.getvalue())
            self.assertEqual(list(graph.nodes()), [])

    def test_private_name_does_not_overwrite_a_public_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory)
            first.write_text("module\nnamespace N\npublic def helper := 1\nend N\n")
            report = sync(graph, lean_paths=[first, second], root=root)
            self.assertEqual(report["lean"], 2)
            self.assertEqual(report["warnings"], [])
            self.assertFalse(graph.get_node(graph.resolve("decl:Lean/A.lean::N.helper")).meta.get("private"))
            self.assertTrue(graph.get_node(graph.resolve("decl:Lean/B.lean::N.helper")).meta["private"])

    def test_qualified_binding_is_order_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory)
            for filename, expected_status in [("A", "lean_ok"), ("B", "sorry")]:
                bp = self.blueprint(root, f"Lean/{filename}.lean::N.helper")
                for paths in [[first, second], [second, first]]:
                    sync(graph, blueprint=str(bp), lean_paths=paths, root=root)
                    entry = collect_one(graph, node_id("bp", "bound"))
                    self.assertEqual(entry["lean_status"], expected_status)
                    self.assertEqual(len(entry["lean"]), 1)
                    self.assertEqual(entry["lean"][0]["file"], f"Lean/{filename}.lean")

    def test_unambiguous_private_binding_and_id_stay_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, _second, graph = self.make_sources(directory)
            bp = self.blueprint(root, "N.helper")
            sync(graph, blueprint=str(bp), lean_paths=[first], root=root)
            self.assertEqual(graph.resolve("decl:N.helper"), node_id("lean", "N.helper"))
            self.assertEqual(graph.resolve("decl:Lean/A.lean::N.helper"), node_id("lean", "N.helper"))
            self.assertEqual(collect_one(graph, node_id("bp", "bound"))["lean_status"], "lean_ok")

    def test_public_duplicates_are_reported_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory, public=True)
            report = sync(graph, lean_paths=[first, second], root=root)
            self.assertEqual(report["lean"], 2)
            self.assertEqual(sum("appears more than once" in w for w in report["warnings"]), 1)
            with self.assertRaisesRegex(HGraphError, "ambiguous Lean binding"):
                sync(graph, blueprint=str(self.blueprint(root, "N.helper")),
                     lean_paths=[first, second], root=root)

    def test_qualified_public_binding_is_not_reported_as_unattached(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, _second, graph = self.make_sources(directory, public=True)
            report = sync(graph, blueprint=str(self.blueprint(root, "Lean/A.lean::N.helper")),
                          lean_paths=[first], root=root)
            self.assertEqual(report["warnings"], [])

    def test_old_merged_record_and_annotations_are_retained_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root, first, second, graph = self.make_sources(directory)
            original = node_id("lean", "N.helper")
            graph.add_node("N.helper", id=original, type="lean", generated="lean", decl="N.helper",
                           source_key="Lean/B.lean::N.helper", file="Lean/B.lean", tag="retained")
            graph.add_attachment(original, "comment", "Existing source review")
            sync(graph, lean_paths=[first, second], root=root)
            self.assertTrue(graph.get_node(original).meta["stale"])
            self.assertEqual(graph.get_node(original).meta["tag"], "retained")
            self.assertEqual(len(graph.attachments(original, "comment")), 1)
            self.assertNotEqual(graph.resolve("decl:Lean/B.lean::N.helper"), original)
            # Returning to a unique name must not make stale split records
            # ambiguous with its active, backwards-compatible ID.
            sync(graph, lean_paths=[first], root=root)
            self.assertEqual(graph.resolve("decl:N.helper"), original)
            self.assertFalse(graph.get_node(original).meta.get("stale"))


if __name__ == "__main__":
    unittest.main()
