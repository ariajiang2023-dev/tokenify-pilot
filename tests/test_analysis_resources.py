"""Resource setup verifies downloaded bytes before creating analysis caches."""

import hashlib
import importlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from urllib.error import URLError
import zipfile


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        for name, data in entries.items():
            handle.writestr(name, data)
    return buffer.getvalue()


class ResourceSetupTests(unittest.TestCase):
    def setup_module(self):
        path = Path(__file__).resolve().parents[1] / "scripts/prepare_resources.py"
        self.assertTrue(path.exists(), "Resource bootstrap has not been implemented")
        return importlib.import_module("scripts.prepare_resources")

    def test_verified_download_is_cached_without_repeated_network_calls(self):
        setup = self.setup_module()
        calls = []
        blob = b"verified source\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache/source.jsonl"
            setup.download_verified("https://example.test/source", digest(blob), path,
                                    fetch=lambda url: calls.append(url) or blob)
            self.assertEqual(path.read_bytes(), blob)
            setup.download_verified("https://example.test/source", digest(blob), path,
                                    fetch=lambda url: self.fail("Cached source must not download"))
        self.assertEqual(len(calls), 1)

    def test_digest_mismatch_keeps_existing_cache_unchanged(self):
        setup = self.setup_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jsonl"
            path.write_bytes(b"existing bytes")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                setup.download_verified("https://example.test/source", digest(b"wanted"), path,
                                        force=True, fetch=lambda url: b"incorrect")
            self.assertEqual(path.read_bytes(), b"existing bytes")

    def test_network_failure_creates_no_partial_file(self):
        setup = self.setup_module()
        def failed_download(url):
            raise URLError("offline")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jsonl"
            with self.assertRaises(URLError):
                setup.download_verified("https://example.test/source", digest(b"wanted"), path,
                                        fetch=failed_download)
            self.assertFalse(path.exists())

    def test_changed_existing_cache_requires_explicit_force(self):
        setup = self.setup_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jsonl"
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "force"):
                setup.download_verified("https://example.test/source", digest(b"wanted"), path,
                                        fetch=lambda url: self.fail("Do not silently replace cache"))

    def test_extracts_only_expected_verified_dictionary_members(self):
        setup = self.setup_module()
        entries = {"cmudict/cmudict": b"A 1 AH0\n", "cmudict/README": b"Copyright and license\n"}
        hashes = {name: digest(data) for name, data in entries.items()}
        with tempfile.TemporaryDirectory() as tmp:
            setup.extract_cmudict(archive(entries), Path(tmp), expected_files=hashes)
            self.assertEqual((Path(tmp) / "cmudict").read_bytes(), entries["cmudict/cmudict"])
            self.assertEqual((Path(tmp) / "LICENSE.txt").read_bytes(), entries["cmudict/README"])

    def test_zip_traversal_and_absolute_paths_are_rejected(self):
        setup = self.setup_module()
        for name in ("../escaped", "/absolute", "cmudict/../../escaped", "cmudict\\escaped"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "Unsafe ZIP"):
                    setup.extract_cmudict(archive({name: b"bad"}), Path(tmp), expected_files={})
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_zip_symlinks_are_rejected(self):
        setup = self.setup_module()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as handle:
            info = zipfile.ZipInfo("cmudict/cmudict")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            handle.writestr(info, "../../escaped")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Unsafe ZIP"):
                setup.extract_cmudict(buffer.getvalue(), Path(tmp), expected_files={})

    def test_all_member_hashes_are_checked_before_extraction(self):
        setup = self.setup_module()
        entries = {"cmudict/cmudict": b"dictionary", "cmudict/README": b"wrong license"}
        hashes = {"cmudict/cmudict": digest(b"dictionary"), "cmudict/README": digest(b"expected license")}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                setup.extract_cmudict(archive(entries), Path(tmp), expected_files=hashes)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
