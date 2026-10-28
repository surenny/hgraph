from pathlib import Path
import unittest

from hgraph.graph import HGraphError
from hgraph.layout import _Model, _dot_chapter_overview
from hgraph.site import _project_site_options


def example(metric=None):
    data = {
        'entries': [
            {'id': 'input', 'title': 'Input', 'kind': 'theorem', 'lean_status': 'sorry', 'deps': []},
            {'id': 'proof', 'title': 'Proof', 'kind': 'theorem', 'lean_status': 'lean_ok',
             'deps': [{'id': 'input', 'type': 'uses'}]},
            {'id': 'library', 'title': 'Library', 'kind': 'theorem', 'lean_status': 'mathlib_ok', 'deps': []},
            {'id': 'unbound', 'title': 'Unbound', 'kind': 'theorem', 'lean_status': 'empty', 'deps': []},
        ],
        'loc': {'input': 0, 'proof': 1, 'library': 1, 'unbound': 1},
        'chapters': [{'num': '1', 'title': 'Inputs'}, {'num': '2', 'title': 'Deductions'}],
    }
    if metric is not None:
        data['graphProgress'] = metric
    return data


class GraphProgressTests(unittest.TestCase):
    def test_config_is_optional_and_rejects_unknown_metrics(self):
        self.assertNotIn('graphProgress', _project_site_options({}, base=Path('.')))
        for metric in ('local', 'closure'):
            self.assertEqual(_project_site_options({'graph_progress': metric}, base=Path('.'))['graphProgress'], metric)
        for metric in (None, True, 0, [], {}, 'local-ish'):
            with self.subTest(metric=metric), self.assertRaisesRegex(HGraphError, 'graph_progress'):
                _project_site_options({'graph_progress': metric}, base=Path('.'))

    def test_local_aggregate_counts_local_proofs_without_closing_their_inputs(self):
        default = _Model(example())
        local = _Model(example('local'))
        self.assertEqual(default.closed, [False, False, True, False])
        self.assertEqual(local.closed, default.closed)
        self.assertEqual(local.proof, default.proof)
        self.assertEqual(local.proof[1], 'local')
        self.assertEqual(local.progress_done, [False, True, True, False])
        self.assertIn('3 statements · 67% local', _dot_chapter_overview(local))
        self.assertIn('3 statements · 33%', _dot_chapter_overview(default))
        self.assertEqual(_dot_chapter_overview(default), _dot_chapter_overview(_Model(example('closure'))))

    def test_empty_graph_has_no_spurious_percentage(self):
        self.assertNotIn('statements ·', _dot_chapter_overview(_Model({'entries': [], 'graphProgress': 'local'})))


if __name__ == '__main__':
    unittest.main()
