"""Exercise the Dockerfiles' Debian preparation without Docker or network I/O."""

from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def apt_run(role):
    text = (ROOT / f'deploy/Dockerfile.{role}').read_text(encoding='utf-8')
    # Docker joins escaped lines before passing a RUN instruction to /bin/sh.
    instructions = text.replace('\\\n', ' ').splitlines()
    blocks = [line[4:] for line in instructions if line.startswith('RUN ') and 'apt-get' in line]
    if len(blocks) != 1:
        raise AssertionError('Expected one complete APT installation layer')
    tokens = shlex.split(blocks[0])
    if tokens[:2] != ['python', '-c']:
        raise AssertionError('Expected Python source preparation before APT')
    return tokens


class DockerBuildPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.apt = root / 'apt'
        (self.apt / 'sources.list.d').mkdir(parents=True)
        self.cert = root / 'ca-certificates.crt'
        self.cert.write_text('synthetic trusted bundle fixture', encoding='utf-8')

    def prepare_sources(self, role):
        locations = {'/etc/apt': self.apt, '/etc/ssl/certs/ca-certificates.crt': self.cert}
        code = compile(apt_run(role)[2], f'Dockerfile.{role}:apt-preparation', 'exec')
        # Execute the actual checked-in preparation statement against only the
        # temporary sources and certificate. No apt command is executed.
        with patch('pathlib.Path', side_effect=lambda value: locations[value]):
            exec(code, {})

    def test_both_images_upgrade_legacy_and_deb822_debian_sources_only(self):
        source_files = {
            'sources.list': 'deb http://deb.debian.org/debian bookworm main\n',
            'sources.list.d/debian.sources': 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie\n',
            'sources.list.d/security.list': 'deb http://security.debian.org/debian-security bookworm-security main\n',
            'sources.list.d/third-party.list': 'deb http://packages.example.test/stable release main\n',
            'sources.list.d/ignored.txt': 'http://deb.debian.org/debian\n',
        }
        for role in ('api', 'worker'):
            with self.subTest(role=role):
                for name, content in source_files.items():
                    (self.apt / name).write_text(content, encoding='utf-8')
                self.prepare_sources(role)
                for name, content in source_files.items():
                    expected = content if name.endswith('.txt') else content.replace(
                        'http://deb.debian.org/', 'https://deb.debian.org/').replace(
                        'http://security.debian.org/', 'https://security.debian.org/')
                    self.assertEqual((self.apt / name).read_text(encoding='utf-8'), expected)

    def test_already_https_sources_are_unchanged(self):
        source = self.apt / 'sources.list.d/debian.sources'
        original = b'Types: deb\nURIs: https://deb.debian.org/debian\nSuites: bookworm\n'
        source.write_bytes(original)
        for role in ('api', 'worker'):
            with self.subTest(role=role):
                self.prepare_sources(role)
                self.assertEqual(source.read_bytes(), original)

    def test_missing_or_empty_ca_bundle_fails_before_source_mutation(self):
        source = self.apt / 'sources.list'
        original = b'deb http://deb.debian.org/debian bookworm main\n'
        source.write_bytes(original)
        for role in ('api', 'worker'):
            for absent in (False, True):
                with self.subTest(role=role, absent=absent):
                    self.cert.write_bytes(b'')
                    if absent:
                        self.cert.unlink()
                    with self.assertRaisesRegex(AssertionError, 'Trusted CA bundle is missing'):
                        self.prepare_sources(role)
                    self.assertEqual(source.read_bytes(), original)

    def test_missing_expected_debian_source_fails(self):
        (self.apt / 'sources.list').write_text('deb https://packages.example.test stable main\n', encoding='utf-8')
        for role in ('api', 'worker'):
            with self.subTest(role=role), self.assertRaisesRegex(AssertionError, 'Expected Debian sources are missing'):
                self.prepare_sources(role)

    def test_update_and_install_use_bounded_retry_timeout_and_fail_on_partial_update(self):
        for role in ('api', 'worker'):
            with self.subTest(role=role):
                tokens = apt_run(role)
                commands = []
                start = 0
                for index, token in enumerate(tokens):
                    if token == '&&':
                        commands.append(tokens[start:index])
                        start = index + 1
                commands.append(tokens[start:])
                apt = [command for command in commands if command[0] == 'apt-get']
                self.assertEqual(len(apt), 2)
                for command in apt:
                    options = [command[index + 1] for index, value in enumerate(command) if value == '-o']
                    self.assertEqual(dict(value.split('=', 1) for value in options), {
                        'Acquire::Retries': '3', 'Acquire::http::Timeout': '30', 'Acquire::https::Timeout': '30',
                    })
                self.assertIn('update', apt[0])
                self.assertIn('--error-on=any', apt[0])
                self.assertIn('install', apt[1])
                self.assertIn('--no-install-recommends', apt[1])
                self.assertIn('ffmpeg', apt[1])
                self.assertIn('fonts-noto-cjk', apt[1])
                self.assertEqual(commands[-1], ['rm', '-rf', '/var/lib/apt/lists/*'])


if __name__ == '__main__':
    unittest.main()
