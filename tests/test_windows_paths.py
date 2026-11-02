import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from home_runtime import instance


class MaterializePathTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows dependency copying')
    def test_materialize_preserves_deep_files_on_first_and_second_launch(self):
        base = Path(tempfile.mkdtemp(prefix="xiaoyu-path-test-"))
        try:
            source = base / 'input'
            for group in instance.SOURCE_GROUPS:
                (source / group).mkdir(parents=True)
            relative = Path('qq-bridge/node_modules') / ('dependency-' + 'a' * 60) / ('nested-' + 'b' * 60) / 'original.js'
            original = instance._filesystem_path(source / relative)
            original.parent.mkdir(parents=True)
            original.write_bytes(b'// synthetic dependency; no network or external actions\n')
            target = base / ('instance-' + 'c' * 70)
            with patch.object(instance, 'source_root', return_value=source):
                workspace, changed = instance.materialize(target)
                copied = instance._filesystem_path(workspace / relative)
                self.assertGreater(len(str(copied)), 260)
                self.assertEqual(copied.read_bytes(), original.read_bytes())
                self.assertIn(relative.as_posix(), changed)
                _, repeated = instance.materialize(target)
                self.assertEqual(repeated, [])

        finally:
            cleanup = instance._filesystem_path(base)
            self.assertEqual(cleanup, instance._filesystem_path(base.resolve()))
            self.assertTrue(base.name.startswith("xiaoyu-path-test-"))
            shutil.rmtree(cleanup)
