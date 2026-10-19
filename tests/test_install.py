import contextlib
import base64
import hashlib
import os
import subprocess
import sys
import tarfile
from unittest.mock import patch
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='review install ')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.codex = self.home / 'custom-codex'
        self.project = self.home / 'project'
        self.project.mkdir()

    def apply(self, main='both', council=False, reviewer=None):
        plan = install.plan_install(self.home, self.codex, main, self.project, council, reviewer)
        with contextlib.redirect_stdout(io.StringIO()):
            for path, content, mode in plan: install.put(path, content, mode)
        return plan

    def test_repeat_install_preserves_content_and_is_idempotent(self):
        self.codex.mkdir()
        p = self.codex / 'AGENTS.md'; p.write_text('# My existing instructions\nKeep my settings.\n')
        self.apply()
        first = p.read_text(); backup_count = len(list(self.codex.glob('*.backup-*')))
        self.apply()
        self.assertEqual(p.read_text(), first)
        self.assertEqual(len(list(self.codex.glob('*.backup-*'))), backup_count)
        self.assertIn('Keep my settings.', first)
        self.assertEqual(first.count(install.BEGIN), 1)
        self.assertIn('NOT required after every prompt', first)
        cursor = (self.project / '.cursor/rules/codex-cursor-review.mdc').read_text()
        self.assertTrue(cursor.startswith('---\n'))
        self.assertIn('--reviewer codex', cursor)
        self.assertIn('--reviewer cursor', first)

    def test_mcp_merge_preserves_other_servers_and_codex_model(self):
        self.codex.mkdir()
        (self.codex / 'config.toml').write_text('model = "keep-me"\n[mcp_servers.other]\ncommand = "other"\n')
        (self.home / '.cursor').mkdir()
        (self.home / '.cursor/mcp.json').write_text(json.dumps({'mcpServers': {'other': {'command': 'keep'}}}))
        self.apply(council=True)  # plans configuration only; no network download in tests
        cfg = tomllib.loads((self.codex / 'config.toml').read_text())
        self.assertEqual(cfg['model'], 'keep-me')
        self.assertEqual(cfg['mcp_servers']['other']['command'], 'other')
        cursor = json.loads((self.home / '.cursor/mcp.json').read_text())
        self.assertEqual(cursor['mcpServers']['other']['command'], 'keep')
        self.apply(council=True)
        self.assertEqual((self.codex / 'config.toml').read_text().count('[mcp_servers.council]'), 1)

    def test_conflicting_council_refuses_before_any_writes(self):
        self.codex.mkdir()
        (self.codex / 'config.toml').write_text('[mcp_servers.council]\ncommand = "existing-server"\n')
        with self.assertRaises(ValueError): self.apply(council=True)
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())

    def test_unmarked_legacy_policy_refuses(self):
        self.codex.mkdir()
        (self.codex / 'AGENTS.md').write_text('# Independent Cursor review required\nLegacy always-review policy.\n')
        with self.assertRaises(ValueError): self.apply()
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())

    def test_existing_user_model_preserved(self):
        p = self.home / '.config/codex-cursor-review/config.json'
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'model': 'user-chosen-model'}))
        self.apply()
        self.assertEqual(json.loads(p.read_text())['reviewers']['cursor']['model'], 'user-chosen-model')

    def test_cursor_requires_project(self):
        with self.assertRaises(ValueError):
            install.plan_install(self.home, self.codex, 'cursor', None, False)

    def test_all_requires_project(self):
        with self.assertRaises(ValueError):
            install.plan_install(self.home, self.codex, 'all', None, False)

    def test_codex_main_policy_stays_on_cursor(self):
        self.codex.mkdir()
        self.apply(main='codex')
        text = (self.codex / 'AGENTS.md').read_text()
        self.assertIn('--reviewer cursor', text)
        self.assertIn('claude-opus-5-5-medium', text)
        self.assertNotIn('gpt-6-astra', text)
        self.assertFalse((self.home / '.claude/CLAUDE.md').exists())

    def test_claude_install_pins_astra_medium_and_is_idempotent(self):
        self.apply(main='claude')
        path = self.home / '.claude/CLAUDE.md'
        text = path.read_text()
        self.assertIn('--reviewer codex --model gpt-6-astra --effort medium', text)
        self.assertIn('The main agent is claude', text)
        self.assertNotIn('--reviewer cursor', text)
        self.assertEqual(text.count(install.BEGIN), 1)
        self.apply(main='claude')
        self.assertEqual(path.read_text(), text)
        self.assertEqual(list(path.parent.glob('*.backup-*')), [])
        cfg = json.loads((self.home / '.config/codex-cursor-review/config.json').read_text())
        self.assertEqual(cfg['default_reviewer'], 'codex')
        self.assertIsNone(cfg['reviewers']['codex']['model'])
        self.assertEqual(cfg['reviewers']['codex']['effort'], 'medium')
        self.assertEqual(cfg['reviewers']['cursor']['model'], 'claude-opus-5-5-medium')

    def test_claude_preserves_existing_instructions(self):
        path = self.home / '.claude/CLAUDE.md'
        path.parent.mkdir(parents=True)
        path.write_text('# My Claude instructions\nKeep this.\n')
        self.apply(main='claude')
        text = path.read_text()
        self.assertIn('Keep this.', text)
        self.assertIn('gpt-6-astra', text)
        self.assertEqual(text.count(install.BEGIN), 1)

    def test_all_installs_every_main_policy(self):
        self.codex.mkdir()
        self.apply(main='all')
        self.assertIn('--reviewer cursor', (self.codex / 'AGENTS.md').read_text())
        cursor = (self.project / '.cursor/rules/codex-cursor-review.mdc').read_text()
        self.assertIn('--reviewer codex', cursor)
        self.assertNotIn('gpt-6-astra', cursor)
        claude = (self.home / '.claude/CLAUDE.md').read_text()
        self.assertIn('--model gpt-6-astra', claude)
        self.assertIn('--effort medium', claude)

    def test_claude_council_preserves_other_settings(self):
        path = self.home / '.claude.json'
        path.write_text(json.dumps({'numStartups': 3, 'mcpServers': {'other': {'command': 'keep'}}}))
        self.codex.mkdir()
        (self.codex / 'config.toml').write_text('model = "keep-me"\n')
        self.apply(main='claude', council=True)
        data = json.loads(path.read_text())
        self.assertEqual(data['numStartups'], 3)
        self.assertEqual(data['mcpServers']['other']['command'], 'keep')
        self.assertEqual(data['mcpServers']['council']['args'], ['mcp', '-n', 'Claude Code'])
        compact = json.dumps(data, separators=(',', ':'))
        path.write_text(compact)
        self.apply(main='claude', council=True)
        self.assertEqual(path.read_text(), compact)

    def test_claude_council_conflict_refuses(self):
        path = self.home / '.claude.json'
        path.write_text(json.dumps({'mcpServers': {'council': {'command': 'existing'}}}))
        with self.assertRaises(ValueError):
            self.apply(main='claude', council=True)
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())
        self.assertEqual(json.loads(path.read_text())['mcpServers']['council']['command'], 'existing')

    def test_claude_json_list_refuses(self):
        path = self.home / '.claude.json'
        path.write_text('[]')
        with self.assertRaises(ValueError):
            self.apply(main='claude', council=True)
        self.assertEqual(path.read_text(), '[]')

    def test_malformed_markers_refuse(self):
        for text in [install.BEGIN, install.END, install.END + install.BEGIN, install.BEGIN * 2 + install.END * 2]:
            with self.assertRaises(ValueError): install.managed_text(text, 'new policy')

    def test_symlink_refuses(self):
        self.codex.mkdir()
        target = self.home / 'valuable.txt'; target.write_text('original')
        (self.codex / 'AGENTS.md').symlink_to(target)
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(target.read_text(), 'original')

    def test_equivalent_command_symlinks_are_preserved(self):
        self.codex.mkdir()
        alias = self.home / 'home-alias'
        alias.symlink_to(self.home, target_is_directory=True)
        command = str(alias / '.local/share/agents-council/0.4.0/package/council')
        (self.codex / 'config.toml').write_text('[mcp_servers.council]\ncommand = ' + json.dumps(command) + '\nargs = ["mcp", "-n", "Codex"]\n')
        self.apply(council=True)
        cfg = tomllib.loads((self.codex / 'config.toml').read_text())
        self.assertEqual(cfg['mcp_servers']['council']['command'], command)

    def test_inline_toml_table_refuses_without_modification(self):
        self.codex.mkdir()
        p = self.codex / 'config.toml'
        original = 'mcp_servers = { other = { command = "keep" } }\n'
        p.write_text(original)
        with self.assertRaises(ValueError): self.apply(council=True)
        self.assertEqual(p.read_text(), original)
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())

    def test_astral_unicode_home_produces_valid_toml(self):
        self.home = self.home / 'server-🌍'
        self.apply(main='codex', council=True)
        config = tomllib.loads((self.codex / 'config.toml').read_text())
        self.assertIn('🌍', config['mcp_servers']['council']['command'])

    def test_legacy_header_with_managed_block_still_refuses(self):
        existing = install.managed_text('', 'new policy') + '\n# Independent Cursor review required\nold'
        with self.assertRaises(ValueError): install.managed_text(existing, 'newer policy')

    def test_python_path_with_spaces_refuses(self):
        with patch.object(sys, 'executable', '/tmp/python venv/bin/python'):
            with self.assertRaises(ValueError): self.apply()

    def test_old_kernel_shebang_limit(self):
        with patch.object(sys, 'executable', '/' + 'a' * 125):
            with self.assertRaises(ValueError): self.apply()

    def test_cursor_json_list_refuses(self):
        p = self.home / '.cursor/mcp.json'; p.parent.mkdir()
        p.write_text('[]')
        with self.assertRaises(ValueError): self.apply(council=True)
        self.assertEqual(p.read_text(), '[]')

    def test_cursor_json_conflict_refuses(self):
        p = self.home / '.cursor/mcp.json'; p.parent.mkdir()
        p.write_text(json.dumps({'mcpServers': {'council': {'command': 'existing'}}}))
        with self.assertRaises(ValueError): self.apply(council=True)
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())

    def test_existing_cursor_json_format_preserved(self):
        self.apply(council=True)
        p = self.home / '.cursor/mcp.json'
        compact = json.dumps(json.loads(p.read_text()), separators=(',', ':'))
        p.write_text(compact)
        self.apply(council=True)
        self.assertEqual(p.read_text(), compact)

    def test_override_warning(self):
        self.codex.mkdir(); (self.codex / 'AGENTS.override.md').write_text('User override')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr): self.apply()
        self.assertIn('takes precedence', stderr.getvalue())
        self.assertEqual((self.codex / 'AGENTS.override.md').read_text(), 'User override')

    def test_cli_dry_run_and_home_isolation(self):
        isolated = self.home / 'isolated'
        env = dict(os.environ, CODEX_HOME=str(self.home / 'must-not-touch'))
        base = [sys.executable, str(install.SOURCE / 'install.py'), '--home', str(isolated)]
        result = subprocess.run(base + ['--dry-run'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(isolated.exists())
        result = subprocess.run(base, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((isolated / '.codex/AGENTS.md').exists())
        self.assertFalse((self.home / 'must-not-touch').exists())


    def test_antigravity_main_installs_gemini_rules_and_is_idempotent(self):
        self.apply(main='antigravity')
        path = self.home / '.gemini/GEMINI.md'
        text = path.read_text()
        self.assertIn('The main agent is antigravity', text)
        self.assertIn('--reviewer codex --model gpt-6-astra --effort medium', text)
        self.assertIn("Antigravity's installed reviewer is Codex GPT-6 Astra", text)
        self.apply(main='antigravity')
        self.assertEqual(path.read_text(), text)
        self.assertFalse((self.home / '.gemini/AGENTS.md').exists())
        cfg = json.loads((self.home / '.config/codex-cursor-review/config.json').read_text())
        self.assertEqual(cfg['default_reviewer'], 'codex')

    def test_antigravity_uses_existing_agents_md_and_preserves_it(self):
        path = self.home / '.gemini/AGENTS.md'; path.parent.mkdir()
        path.write_text('# My global rules\nKeep this.\n')
        self.apply(main='antigravity', reviewer='claude')
        text = path.read_text()
        self.assertIn('Keep this.', text)
        self.assertIn('--reviewer claude --model claude-opus-5-5 --effort medium', text)
        self.assertFalse((self.home / '.gemini/GEMINI.md').exists())

    def test_reviewer_override_for_every_main(self):
        self.codex.mkdir()
        self.apply(main='all', reviewer='codex=gemini,cursor=claude,claude=gemini,antigravity=cursor')
        codex = (self.codex / 'AGENTS.md').read_text()
        self.assertIn('--reviewer gemini --model gemini-4-argon-high --workspace', codex)
        self.assertIn('do not pass `--effort`', codex)
        cursor = (self.project / '.cursor/rules/codex-cursor-review.mdc').read_text()
        self.assertIn('--reviewer claude --model claude-opus-5-5 --effort medium', cursor)
        claude = (self.home / '.claude/CLAUDE.md').read_text()
        self.assertIn('--reviewer gemini --model gemini-4-argon-high', claude)
        self.assertNotIn('gpt-6-astra', claude)
        gemini = (self.home / '.gemini/GEMINI.md').read_text()
        self.assertIn('--reviewer cursor --workspace', gemini)
        cfg = json.loads((self.home / '.config/codex-cursor-review/config.json').read_text())
        self.assertEqual(cfg['default_reviewer'], 'gemini')
        self.assertEqual(cfg['reviewers']['gemini']['model'], 'gemini-4-argon-high')
        self.assertEqual(cfg['reviewers']['claude']['model'], 'claude-opus-5-5')

    def test_single_reviewer_applies_to_all_selected_mains(self):
        self.apply(main='claude,antigravity', reviewer='cursor')
        for path in (self.home / '.claude/CLAUDE.md', self.home / '.gemini/GEMINI.md'):
            self.assertIn('--reviewer cursor --workspace', path.read_text())

    def test_self_review_and_bad_reviewer_refuse_before_writes(self):
        for main, reviewer in [('antigravity', 'gemini'), ('claude', 'claude'), ('codex', 'codex'),
                               ('cursor', 'cursor'), ('claude', 'nobody'), ('claude', 'codex=cursor')]:
            with self.subTest(main=main, reviewer=reviewer):
                with self.assertRaises(ValueError): self.apply(main=main, reviewer=reviewer)
        with self.assertRaises(ValueError): self.apply(main='codex,claude', reviewer='claude')
        with self.assertRaises(ValueError): self.apply(main='claude,unknown')
        with self.assertRaises(ValueError): self.apply(main='claude', reviewer='')
        self.assertFalse((self.home / '.local/bin/codex-cursor-review').exists())
        self.assertFalse((self.home / '.claude/CLAUDE.md').exists())

    def test_antigravity_council_preserves_and_handles_empty_file(self):
        path = self.home / '.gemini/config/mcp_config.json'; path.parent.mkdir(parents=True)
        path.write_text('')
        self.apply(main='antigravity', council=True)
        data = json.loads(path.read_text())
        self.assertEqual(data['mcpServers']['council']['args'], ['mcp', '-n', 'Antigravity'])
        path.write_text(json.dumps({'mcpServers': {'other': {'url': 'https://keep.invalid/mcp'}}}))
        self.apply(main='antigravity', council=True)
        data = json.loads(path.read_text())
        self.assertEqual(data['mcpServers']['other']['url'], 'https://keep.invalid/mcp')
        self.assertIn('council', data['mcpServers'])
        compact = json.dumps(data, separators=(',', ':'))
        path.write_text(compact)
        self.apply(main='antigravity', council=True)
        self.assertEqual(path.read_text(), compact)
        self.assertFalse((self.home / '.claude.json').exists())

    def test_antigravity_council_conflict_refuses(self):
        path = self.home / '.gemini/config/mcp_config.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'mcpServers': {'council': {'command': 'existing'}}}))
        with self.assertRaises(ValueError): self.apply(main='antigravity', council=True)
        self.assertFalse((self.home / '.gemini/GEMINI.md').exists())

    def test_cli_reviewer_flag(self):
        isolated = self.home / 'isolated2'
        base = [sys.executable, str(install.SOURCE / 'install.py'), '--home', str(isolated),
                '--main', 'antigravity', '--reviewer', 'claude']
        result = subprocess.run(base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--reviewer claude', (isolated / '.gemini/GEMINI.md').read_text())
        result = subprocess.run(base[:-1] + ['gemini'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('cannot review its own work', result.stderr)

class CouncilDownloadTests(unittest.TestCase):
    def exercise(self, missing=False, symlink=False, bad_pin=False):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); home = root / 'home'; source = root / 'source'; source.mkdir()
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                for name in ('council', 'LICENSE', 'package.json'):
                    if missing and name == 'LICENSE': continue
                    item = tarfile.TarInfo('package/' + name)
                    if symlink and name == 'council':
                        item.type = tarfile.SYMTYPE; item.linkname = '/tmp/outside'
                        archive.addfile(item)
                    else:
                        content = b'fixture'; item.size = len(content)
                        archive.addfile(item, io.BytesIO(content))
            data = stream.getvalue()
            digest = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
            name = 'agents-council-linux-x64'
            (source / 'council-integrity.json').write_text(json.dumps({'version': '0.4.0', 'packages': {name: 'wrong' if bad_pin else digest}}))
            metadata = {'dist': {'tarball': 'https://example.invalid/pkg', 'integrity': digest}}
            responses = [io.BytesIO(json.dumps(metadata).encode()), io.BytesIO(data)]
            with patch.object(install, 'SOURCE', source), patch.object(install, 'council_package', return_value=name), patch.object(install.urllib.request, 'urlopen', side_effect=responses):
                if missing or symlink or bad_pin:
                    with self.assertRaises((ValueError, KeyError)): install.install_council(home)
                    self.assertFalse((home / '.local/share/agents-council').exists())
                else:
                    binary = install.install_council(home)
                    self.assertEqual(binary.read_bytes(), b'fixture')
                    self.assertEqual(binary.stat().st_mode & 0o777, 0o755)

    def test_checksum_pin_mismatch(self): self.exercise(bad_pin=True)
    def test_missing_member_leaves_no_partial_package(self): self.exercise(missing=True)
    def test_nonregular_member_refuses(self): self.exercise(symlink=True)
    def test_valid_pinned_fixture_installs(self): self.exercise()


if __name__ == '__main__': unittest.main()
