import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
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

    def test_codex_astra_model_and_effort_are_forwarded(self):
        command = review.build_command(
            'codex', str(self.fake),
            {'model': 'gpt-6-astra', 'effort': 'medium'}, self.root, self.root)
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-astra')
        self.assertIn('model_reasoning_effort="medium"', command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--force', command)
        self.assertNotIn('--yolo', command)

    def test_cli_model_and_effort_override_config(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'reviewers': {'codex': {
            'model': 'other-model', 'effort': 'low', 'binary': str(self.fake)}}}))
        self.fake.write_text(
            '#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
            'assert sys.argv[sys.argv.index("--model") + 1] == "gpt-6-astra"\n'
            'assert \'model_reasoning_effort="medium"\' in sys.argv\n'
            'Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text('
            '"No blockers.\\nREADY FOR MAIN AGENT VERIFICATION\\n")\n',
            encoding='utf-8')
        self.fake.chmod(0o755)
        prompt = self.root / 'prompt.md'
        prompt.write_text('Review the change.\n', encoding='utf-8')
        argv = ['review.py', '--reviewer', 'codex', '--config', str(config),
                '--workspace', str(self.root), '--prompt-file', str(prompt),
                '--output-dir', str(self.root / 'runs'),
                '--model', 'gpt-6-astra', '--effort', 'medium']
        with patch.object(sys, 'argv', argv):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = review.main()
        self.assertEqual(code, 0)
        meta = json.loads(next((self.root / 'runs').glob('*/metadata.json')).read_text())
        self.assertEqual(meta['requested_model'], 'gpt-6-astra')
        self.assertEqual(meta['requested_effort'], 'medium')

    def test_blank_effort_refuses(self):
        config = self.root / 'config.json'
        config.write_text('{}\n', encoding='utf-8')
        argv = ['review.py', '--reviewer', 'codex', '--config', str(config), '--effort', '   ', '--check']
        with patch.object(sys, 'argv', argv):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = review.main()
        self.assertEqual(code, 1)

    def test_model_override_is_literal_argument(self):
        value = 'model[$(touch sentinel)]'
        cmd = review.build_command('cursor', str(self.fake), {'model': value}, self.root, self.root)
        self.assertEqual(cmd[cmd.index('--model') + 1], value)
        self.assertFalse((self.root / 'sentinel').exists())

    def test_gemini_agy_headless_stdin_and_stdout_review(self):
        meta, run = self.run_case('''assert '--disable-slash-commands' in sys.argv
assert sys.argv[sys.argv.index('--model') + 1] == 'fixture-model'
assert not {'-p', '--print', '--prompt', '--mode', '--dangerously-skip-permissions'} & set(sys.argv)
prompt = sys.stdin.read()
assert prompt.startswith('TOOL CONSTRAINT FOR THIS RUN: use ONLY the view_file tool') and 'Review α experiment.' in prompt
print('No blockers.\\nREADY FOR MAIN AGENT VERIFICATION')''', 0, reviewer='gemini')
        self.assertEqual(meta['verdict'], 'READY FOR MAIN AGENT VERIFICATION')
        self.assertIn('No blockers.', (run / 'review.md').read_text())
        self.assertFalse((run / 'stdout.txt').exists())
        self.assertIn('TOOL CONSTRAINT', (run / 'request.md').read_text())

    def test_gemini_denied_tool_empty_output_blocks_and_shows_stderr(self):
        self.fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdin.read();print('jetski: no output produced', file=sys.stderr)\n", encoding='utf-8')
        self.fake.chmod(0o755)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            result = review.run_review('gemini', self.options, self.root, 'Review.', self.root / 'runs', 2)
        self.assertEqual(result, 1)
        self.assertIn('jetski: no output produced', err.getvalue())
        meta = json.loads(next((self.root / 'runs').glob('*/metadata.json')).read_text())
        self.assertEqual(meta['reason'], 'empty_review')

    def test_missing_model_refused_before_check_or_run(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'reviewers': {'gemini': {'model': None, 'binary': str(self.fake)}}}))
        prompt = self.root / 'prompt.md'
        prompt.write_text('Review.\n', encoding='utf-8')
        for extra in (['--check'], ['--prompt-file', str(prompt), '--output-dir', str(self.root / 'runs')]):
            argv = ['review.py', '--reviewer', 'gemini', '--config', str(config), '--workspace', str(self.root)] + extra
            with patch.object(sys, 'argv', argv):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(review.main(), 1)
        self.assertFalse((self.root / 'runs').exists())

    def test_tool_note_only_for_gemini(self):
        meta, run = self.run_case("sys.stdin.read();print('READY FOR MAIN AGENT VERIFICATION')", 0)
        self.assertNotIn('TOOL CONSTRAINT', (run / 'request.md').read_text())

    def test_gemini_default_model_is_argon_high(self):
        cfg = review.load_config(self.root / 'missing.json')
        self.assertEqual(cfg['reviewers']['gemini']['model'], 'gemini-4-argon-high')
        command = review.build_command('gemini', str(self.fake), cfg['reviewers']['gemini'], self.root, self.root)
        self.assertEqual(command[command.index('--model') + 1], 'gemini-4-argon-high')

    def test_gemini_refuses_effort_and_missing_model(self):
        with self.assertRaises(ValueError):
            review.build_command('gemini', str(self.fake), {'model': 'm', 'effort': 'high'}, self.root, self.root)
        with self.assertRaises(ValueError):
            review.build_command('gemini', str(self.fake), {'model': None}, self.root, self.root)
        config = self.root / 'config.json'
        config.write_text('{}\n', encoding='utf-8')
        prompt = self.root / 'prompt.md'
        prompt.write_text('Review.\n', encoding='utf-8')
        argv = ['review.py', '--reviewer', 'gemini', '--config', str(config), '--effort', 'high',
                '--workspace', str(self.root), '--prompt-file', str(prompt),
                '--output-dir', str(self.root / 'runs')]
        with patch.object(sys, 'argv', argv):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = review.main()
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'runs').exists())

    def test_gemini_default_reviewer_from_config(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'default_reviewer': 'gemini', 'reviewers': {'gemini': {'binary': str(self.fake)}}}))
        self.fake.write_text('#!/usr/bin/env python3\nimport sys\n'
                             'assert sys.argv[sys.argv.index("--model") + 1] == "gemini-4-argon-high"\n'
                             'sys.stdin.read()\nprint("Bug.\\nCHANGES REQUIRED")\n', encoding='utf-8')
        self.fake.chmod(0o755)
        prompt = self.root / 'prompt.md'
        prompt.write_text('Review.\n', encoding='utf-8')
        argv = ['review.py', '--config', str(config), '--workspace', str(self.root),
                '--prompt-file', str(prompt), '--output-dir', str(self.root / 'runs')]
        with patch.object(sys, 'argv', argv):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = review.main()
        self.assertEqual(code, 0)
        meta = json.loads(next((self.root / 'runs').glob('*/metadata.json')).read_text())
        self.assertEqual((meta['reviewer'], meta['verdict']), ('gemini', 'CHANGES REQUIRED'))

    def test_claude_code_reviewer_is_read_only_and_uses_stdin(self):
        self.options['effort'] = 'medium'
        meta, run = self.run_case('''assert sys.argv[1] == '--print'
assert sys.argv[sys.argv.index('--permission-mode') + 1] == 'plan'
assert sys.argv[sys.argv.index('--tools') + 1] == 'Read,Grep,Glob'
assert '--strict-mcp-config' in sys.argv and '--no-session-persistence' in sys.argv
assert sys.argv[sys.argv.index('--model') + 1] == 'fixture-model'
assert sys.argv[sys.argv.index('--effort') + 1] == 'medium'
assert not {'bypassPermissions', '--dangerously-skip-permissions', 'Bash', 'Edit', 'Write'} & set(sys.argv)
prompt = sys.stdin.read()
assert 'Review α experiment.' in prompt and 'TOOL CONSTRAINT' not in prompt
print('Bug.\\nCHANGES REQUIRED')''', 0, reviewer='claude')
        self.assertEqual(meta['verdict'], 'CHANGES REQUIRED')
        self.assertEqual(meta['requested_effort'], 'medium')

    def test_claude_code_defaults_and_optional_model(self):
        cfg = review.load_config(self.root / 'missing.json')
        self.assertEqual(cfg['reviewers']['claude'], {'model': 'claude-opus-5-5', 'effort': 'medium'})
        command = review.build_command('claude', 'claude', {'model': None}, self.root, self.root)
        self.assertNotIn('--model', command)
        self.assertNotIn('--effort', command)

    def test_blank_model_override_refuses(self):
        config = self.root / 'config.json'
        config.write_text('{}\n', encoding='utf-8')
        argv = ['review.py', '--reviewer', 'codex', '--config', str(config), '--model', '  ', '--check']
        with patch.object(sys, 'argv', argv):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(review.main(), 1)

    def test_invalid_profile_shapes_are_clean_errors(self):
        p = self.root / 'config.json'
        for value in [{'reviewers': []}, {'reviewers': {'cursor': {'binary': 3}}}, {'reviewers': {'codex': {'model': []}}}]:
            p.write_text(json.dumps(value))
            with self.assertRaises(ValueError): review.load_config(p)


if __name__ == '__main__': unittest.main()
