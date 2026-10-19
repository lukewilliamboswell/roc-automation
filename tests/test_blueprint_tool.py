import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'actions/blueprint-tool/blueprint_tool.py'
spec = importlib.util.spec_from_file_location('blueprint_tool', SCRIPT)
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


class BlueprintToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.program = self.root/'actual utility'
        self.program.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit "${UTILITY_STATUS:-0}"\n')
        self.program.chmod(0o755)
        self.blueprint = self.root/'blueprint'
        self.blueprint.write_text('#!/bin/sh\nprintf "%s\\n" "$UTILITY_PATH"\n')
        self.blueprint.chmod(0o755)
        self.env = {'GITHUB_WORKSPACE':str(self.root), 'RUNNER_TEMP':str(self.root), 'GITHUB_OUTPUT':str(self.root/'outputs'), 'INPUT_TASK':'docs-path', 'INPUT_BLUEPRINT_BINARY':str(self.blueprint), 'UTILITY_PATH':str(self.program)}

    def test_handoff_survives_task_path_removal_and_wrapper_preserves_arguments_and_exit_status(self):
        self.env['INPUT_MEASURE'] = 'true'
        transient = self.root/'.blueprint/run/task'
        self.blueprint.write_text('#!/bin/sh\nmkdir -p "$TASK_PATH"\nln -s "$UTILITY_PATH" "$TASK_PATH/utility"\nrealpath "$TASK_PATH/utility"\nrm -r "$TASK_PATH"\n')
        self.env['TASK_PATH'] = str(transient)
        with patch.dict(os.environ, self.env): tool.main()
        outputs = dict(line.split('=',1) for line in (self.root/'outputs').read_text().splitlines())
        self.assertEqual(outputs['executable-path'], str(self.program))
        self.assertEqual(outputs['sha256'], hashlib.sha256(self.program.read_bytes()).hexdigest())
        self.assertEqual(int(outputs['bytes']), self.program.stat().st_size)
        # The task process removed its link before returning to the action.
        self.assertFalse(transient.exists())
        result = subprocess.run([outputs['binary'], 'argument with spaces', '$(touch sentinel)'], capture_output=True, text=True, env={**os.environ,'UTILITY_STATUS':'7'})
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['argument with spaces','$(touch sentinel)'])
        metrics = json.loads(Path(outputs['metrics-path']).read_text())
        self.assertEqual(metrics['exit-code'], 7)
        self.assertGreaterEqual(metrics['seconds'], 0)
        self.assertGreater(metrics['max-rss-kib'], 0)
        self.assertFalse((self.root/'sentinel').exists())

    def test_expired_temporary_path_is_rejected_with_the_realpath_remedy(self):
        self.env['UTILITY_PATH'] = str(self.root/'.blueprint/run/expired/roc-docs')
        with patch.dict(os.environ, self.env), self.assertRaisesRegex(ValueError, 'realpath before'):
            tool.main()
        self.assertFalse((self.root/'outputs').exists())

    def test_multiline_relative_and_control_character_paths_cannot_write_action_outputs(self):
        for value in ['relative/path', str(self.program)+'\nother', str(self.program)+'\rbad']:
            with self.subTest(value=value), patch.dict(os.environ, {**self.env,'UTILITY_PATH':value}), self.assertRaises(ValueError):
                tool.main()
        target = self.root/'unsafe\nname'; target.write_bytes(self.program.read_bytes()); target.chmod(0o755)
        alias = self.root/'alias'; alias.symlink_to(target)
        with patch.dict(os.environ, {**self.env,'UTILITY_PATH':str(alias)}), self.assertRaisesRegex(ValueError, 'control'):
            tool.main()
        self.assertFalse((self.root/'outputs').exists())

    def test_optional_measurement_counts_outputs_separately_from_process_time(self):
        directory = self.root/'site'; directory.mkdir(); (directory/'index.html').write_bytes(b'site'); (directory/'manual.pdf').write_bytes(b'pdf')
        metrics = self.root/'measure.json'
        result = subprocess.run([sys.executable, str(SCRIPT.with_name('measure.py')), '--label','docs', '--metrics',str(metrics), '--output',str(directory), '--',str(self.program),'build'], capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(metrics.read_text())
        self.assertEqual(record['outputs'][str(directory)], {'files':2,'bytes':7})


if __name__ == '__main__': unittest.main()
