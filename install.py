#!/usr/bin/env python3
"""Install the review helper and opt-in agent rules without overwriting user settings."""
import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11+ is required to run this installer')
import tomllib
import urllib.request

SOURCE = Path(__file__).resolve().parent
BEGIN = '<!-- codex-cursor-review:begin -->'
END = '<!-- codex-cursor-review:end -->'
COUNCIL_VERSION = '0.4.0'


def managed_text(existing, body):
    if '# Independent Cursor review required' in existing:
        raise ValueError('Legacy unmarked unconditional review policy found. Back it up and remove only that old section, then rerun (see README migration).')
    if existing.count(BEGIN) != existing.count(END) or existing.count(BEGIN) > 1:
        raise ValueError('malformed managed block; preserve the file and fix its markers first')
    block = BEGIN + '\n' + body.rstrip() + '\n' + END
    if BEGIN in existing:
        start = existing.index(BEGIN)
        end = existing.index(END) + len(END)
        if existing.index(END) < start:
            raise ValueError('managed markers out of order')
        return existing[:start] + block + existing[end:]
    return existing.rstrip() + ('\n\n' if existing.strip() else '') + block + '\n'


def read(path):
    return path.read_text(encoding='utf-8') if path.exists() else ''


def put(path, content, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return
    if path.is_symlink():
        raise ValueError('Refusing to replace symlink: ' + str(path))
    old_mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    if path.exists():
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = path.with_name(path.name + '.backup-' + stamp)
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.chmod(tmp, mode if mode is not None else old_mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print('Updated', path)


def council_package():
    system = platform.system().lower()
    arch = {'x86_64': 'x64', 'amd64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine().lower())
    if system not in ('linux', 'darwin') or arch is None:
        raise ValueError('Council binary supported on Linux/macOS x64/arm64 only')
    return 'agents-council-' + system + '-' + arch


def install_council(home):
    name = council_package()
    with urllib.request.urlopen('https://registry.npmjs.org/' + name + '/' + COUNCIL_VERSION, timeout=60) as f:
        meta = json.load(f)
    with urllib.request.urlopen(meta['dist']['tarball'], timeout=120) as f:
        data = f.read()
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode('ascii')
    pins = json.loads(read(SOURCE / 'council-integrity.json'))
    if (pins['version'] != COUNCIL_VERSION or actual != pins['packages'].get(name)
            or actual != meta['dist']['integrity']):
        raise ValueError('Council package integrity mismatch against repository pin')
    root = home / '.local/share/agents-council' / COUNCIL_VERSION / 'package'
    # Validate every selected member before writing any part of the package.
    payloads = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member_name in ('council', 'LICENSE', 'package.json'):
            item = archive.getmember('package/' + member_name)
            if not item.isfile():
                raise ValueError('Council package member is not a regular file')
            payloads[member_name] = archive.extractfile(item).read()
    if any((root / member_name).is_symlink() for member_name in payloads):
        raise ValueError('Refusing to replace Council symlink')
    root.mkdir(parents=True, exist_ok=True)
    for member_name, content in payloads.items():
        destination = root / member_name
        fd, tmp = tempfile.mkstemp(dir=root)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
            os.chmod(tmp, 0o755 if member_name == 'council' else 0o644)
            os.replace(tmp, destination)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    put(root.parent / 'npm-provenance.json', json.dumps(meta, indent=2) + '\n')
    return root / 'council'


def policy(main, helper):
    reviewer = 'cursor' if main == 'codex' else 'codex'
    # A quoted absolute command is valid even when the user's home contains spaces.
    import shlex
    return (read(SOURCE / 'templates/policy.md').replace('__MAIN__', main)
            .replace('__REVIEWER__', reviewer).replace('__HELPER__', shlex.quote(str(helper))))


def same_server(found, expected):
    if not isinstance(found, dict) or set(found) != set(expected):
        return False
    if found.get('args') != expected['args'] or not isinstance(found.get('command'), str):
        return False
    return Path(found['command']).expanduser().resolve() == Path(expected['command']).resolve()


def plan_install(home, codex_home, main, project, with_council):
    if any(c.isspace() for c in sys.executable) or len(os.fsencode(sys.executable)) > 120:
        raise ValueError('Use a Python executable with a short path containing no whitespace (shebang limitation)')
    helper = home / '.local/bin/codex-cursor-review'
    code = read(SOURCE / 'review.py')
    code = '#!' + sys.executable + '\n' + code.split('\n', 1)[1]
    plan = [(helper, code, 0o755)]
    config_file = home / '.config/codex-cursor-review/config.json'
    import review
    config = review.load_config(config_file)
    config['default_reviewer'] = 'codex' if main == 'cursor' else 'cursor'
    plan.append((config_file, json.dumps(config, indent=2) + '\n', None))
    if main in ('codex', 'both'):
        if (codex_home / 'AGENTS.override.md').exists():
            print('Warning: AGENTS.override.md takes precedence. Merge the installed policy into that file to activate it.', file=sys.stderr)
        path = codex_home / 'AGENTS.md'
        plan.append((path, managed_text(read(path), policy('codex', helper)), None))
    if main in ('cursor', 'both'):
        if project is None or not project.is_dir():
            raise ValueError('--project <existing-directory> is required for Cursor main-agent rules')
        path = project / '.cursor/rules/codex-cursor-review.mdc'
        existing = read(path)
        if existing and BEGIN not in existing:
            raise ValueError('An unrelated Cursor rule exists at ' + str(path))
        header = '---\ndescription: Independent review for substantial code and experiments\nalwaysApply: true\n---\n\n'
        plan.append((path, managed_text(existing or header, policy('cursor', helper)), None))
    if with_council:
        command = str(home / '.local/share/agents-council' / COUNCIL_VERSION / 'package/council')
        path = codex_home / 'config.toml'
        existing = read(path)
        parsed = tomllib.loads(existing)
        entry = {'command': command, 'args': ['mcp', '-n', 'Codex']}
        servers = parsed.get('mcp_servers', {})
        if not isinstance(servers, dict):
            raise ValueError('Unsupported mcp_servers layout; merge Council manually')
        found = servers.get('council')
        if found is not None and not same_server(found, entry):
            raise ValueError('Existing different Codex council settings; preserve/merge them manually')
        if found is None:
            # JSON basic strings/arrays are valid TOML for these values.
            addition = '\n[mcp_servers.council]\ncommand = ' + json.dumps(command, ensure_ascii=False) + '\nargs = ["mcp", "-n", "Codex"]\n'
            merged = existing.rstrip() + '\n' + addition
            try:
                check = tomllib.loads(merged)
            except tomllib.TOMLDecodeError as exc:
                raise ValueError('Cannot extend this TOML layout safely; merge Council manually') from exc
            if check.get('mcp_servers', {}).get('council') != entry:
                raise ValueError('Merged Council entry differs; merge manually')
            plan.append((path, merged, None))
        path = home / '.cursor/mcp.json'
        parsed = json.loads(read(path) or '{}')
        if not isinstance(parsed, dict) or not isinstance(parsed.get('mcpServers', {}), dict):
            raise ValueError('Cursor MCP configuration and mcpServers must be JSON objects')
        entries = parsed.setdefault('mcpServers', {})
        entry = {'command': command, 'args': ['mcp', '-n', 'Cursor']}
        if 'council' in entries and not same_server(entries['council'], entry):
            raise ValueError('Existing different Cursor council settings; preserve/merge them manually')
        if 'council' not in entries:
            entries['council'] = entry
            plan.append((path, json.dumps(parsed, indent=2, ensure_ascii=False) + '\n', None))
    for path, _, _ in plan:
        if path.is_symlink():
            raise ValueError('Refusing to replace symlink: ' + str(path))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', choices=['codex', 'cursor', 'both'], default='codex')
    parser.add_argument('--project', type=Path, help='Install Cursor main-agent rule in this existing project')
    parser.add_argument('--with-council', action='store_true', help='Download pinned Council and register in both clients')
    parser.add_argument('--home', type=Path, help='Installation home (use a temp directory to preview/test)')
    parser.add_argument('--codex-home', type=Path, help='Override CODEX_HOME for Codex configuration')
    parser.add_argument('--dry-run', action='store_true', help='Print destinations; write/download nothing')
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 11) or os.name != 'posix':
            raise ValueError('Python 3.11+ on Linux/macOS/WSL is required')
        home = (args.home or Path.home()).expanduser().resolve()
        codex_home = (args.codex_home.expanduser().resolve() if args.codex_home else
                      (home / '.codex' if args.home else
                       Path(os.environ.get('CODEX_HOME', str(home / '.codex'))).expanduser().resolve()))
        project = args.project.expanduser().resolve() if args.project else None
        plan = plan_install(home, codex_home, args.main, project, args.with_council)
        if args.dry_run:
            for path, _, _ in plan: print('Would update', path)
            if args.with_council: print('Would download verified Council', COUNCIL_VERSION)
            return 0
        if args.with_council:
            install_council(home)
        for path, content, mode in plan:
            put(path, content, mode)
        print('Installed. Reload editors/start new conversations. CLI authentication is separate.')
        print('Check:', home / '.local/bin/codex-cursor-review', '--check')
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        print('Install stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
