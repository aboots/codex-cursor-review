import contextlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

import review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = self.root / 'fake-agent'
        self.options = {'model': 'fixture-model', 'binary': str(self.fake)}

    def run_case(self, body, expected, reviewer='cursor', timeout=2):
        if body is not None:
            self.fake.write_text('#!/usr/bin/env python3\nimport sys\n' + body + '\n', encoding='utf-8')
            self.fake.chmod(0o755)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = review.run_review(reviewer, self.options, self.root, 'Review α experiment.', self.root / 'runs', timeout)
        self.assertEqual(result, expected)
        paths = list((self.root / 'runs').glob('*/metadata.json'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].parent.stat().st_mode & 0o777, 0o700)
        return json.loads(paths[0].read_text()), paths[0].parent

    def test_cursor_stdin_model_and_ask_mode(self):
        meta, run = self.run_case('''assert sys.argv[sys.argv.index('--mode') + 1] == 'ask'
assert sys.argv[sys.argv.index('--model') + 1] == 'fixture-model'
assert '--force' not in sys.argv and '--yolo' not in sys.argv
assert 'Review α experiment.' in sys.stdin.read()
print('No blockers.\\nREADY FOR MAIN AGENT VERIFICATION')''', 0)
        self.assertEqual(meta['verdict'], 'READY FOR MAIN AGENT VERIFICATION')
        self.assertEqual(meta['status'], 'review_returned_requires_main_agent_assessment')

    def test_codex_uses_final_message_and_read_only(self):
        meta, run = self.run_case('''from pathlib import Path
assert sys.argv[sys.argv.index('--sandbox') + 1] == 'read-only'
assert 'approval_policy="never"' in sys.argv
assert sys.argv[-1] == '-'
assert 'Review α experiment.' in sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('No blockers.\\nREADY FOR MAIN AGENT VERIFICATION')
print('progress only, not the review')''', 0, reviewer='codex')
        self.assertNotIn('progress only', (run / 'review.md').read_text())
        self.assertIn('progress only', (run / 'stdout.txt').read_text())

    def test_codex_missing_final_message_blocks(self):
        meta, _ = self.run_case("print('READY FOR MAIN AGENT VERIFICATION')", 1, reviewer='codex')
        self.assertEqual(meta['reason'], 'empty_review')

    def test_changes_required_is_not_approval(self):
        meta, _ = self.run_case("print('Bug here.\\n**CHANGES REQUIRED**')", 0)
        self.assertEqual(meta['verdict'], 'CHANGES REQUIRED')
        self.assertEqual(meta['status'], 'review_returned_requires_main_agent_assessment')

    def test_blocked_verdict(self):
        meta, _ = self.run_case("print('Missing context.\\nBLOCKED')", 2)
        self.assertEqual(meta['reason'], 'reviewer_blocked')

    def test_truncated_review(self):
        meta, _ = self.run_case("print('I will inspect the files.')", 2)
        self.assertEqual(meta['reason'], 'no_final_verdict')

    def test_nonzero_overrides_ready(self):
        meta, _ = self.run_case("print('READY FOR MAIN AGENT VERIFICATION');sys.exit(3)", 3)
        self.assertEqual(meta['status'], 'blocked')

    def test_empty_output(self):
        meta, _ = self.run_case('pass', 1)
        self.assertEqual(meta['reason'], 'empty_review')

    def test_missing_binary(self):
        meta, _ = self.run_case(None, 1)
        self.assertEqual(meta['reason'], 'launch_failed')

    def test_timeout(self):
        meta, _ = self.run_case('import time;time.sleep(30)', 124, timeout=.1)
        self.assertEqual(meta['reason'], 'TimeoutExpired')
        self.assertIsNotNone(meta['child_returncode'])

    def test_exit_race(self):
        kill = os.killpg
        def race(pid, sig):
            kill(pid, sig)
            raise ProcessLookupError('simulated race')
        with patch.object(os, 'killpg', side_effect=race):
            meta, _ = self.run_case('import time;time.sleep(30)', 124, timeout=.1)
        self.assertEqual(meta['status'], 'blocked')

    def test_signal_exit(self):
        meta, _ = self.run_case('import os,signal;os.kill(os.getpid(),signal.SIGTERM)', 143)
        self.assertEqual(meta['child_returncode'], -signal.SIGTERM)

    def test_invalid_timeout(self):
        p = self.root / 'config.json'
        for value in [0, -1, True, '900', float('nan')]:
            p.write_text(json.dumps({'timeout_seconds': value}))
            with self.assertRaises(ValueError): review.load_config(p)

    def test_legacy_config_keeps_user_model(self):
        p = self.root / 'config.json'
        p.write_text(json.dumps({'model': 'chosen-model', 'timeout_seconds': 25}))
        cfg = review.load_config(p)
        self.assertEqual(cfg['reviewers']['cursor']['model'], 'chosen-model')
        self.assertEqual(cfg['timeout_seconds'], 25)

    def test_model_override_is_literal_argument(self):
        value = 'model[$(touch sentinel)]'
        cmd = review.build_command('cursor', str(self.fake), {'model': value}, self.root, self.root)
        self.assertEqual(cmd[cmd.index('--model') + 1], value)
        self.assertFalse((self.root / 'sentinel').exists())

    def test_invalid_profile_shapes_are_clean_errors(self):
        p = self.root / 'config.json'
        for value in [{'reviewers': []}, {'reviewers': {'cursor': {'binary': 3}}}, {'reviewers': {'codex': {'model': []}}}]:
            p.write_text(json.dumps(value))
            with self.assertRaises(ValueError): review.load_config(p)


if __name__ == '__main__': unittest.main()
