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

    def apply(self, main='both', council=False):
        plan = install.plan_install(self.home, self.codex, main, self.project, council)
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
