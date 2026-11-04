"""Download pinned public analysis resources; analysis itself stays offline."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUPPORT = ROOT / "tokenify_analysis/_support"
CMUDICT_URL = "https://raw.githubusercontent.com/nltk/nltk_data/5f09e470fe339e1a119dd174ca284129a3c7a5bb/packages/corpora/cmudict.zip"
CMUDICT_ARCHIVE_SHA256 = "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6"
CMUDICT_FILES = {
    "cmudict/cmudict": "cad209c39eb87677d64e93d97f8eed10b7e6f9bdd42de8e7ca8efc8e17d62e8a",
    "cmudict/README": "b0556be7a2b12bea6a667277b75864f802dd4854480657ce298c62e6897e766b",
}
EXPERTQA_URL = "https://raw.githubusercontent.com/chaitanyamalaviya/ExpertQA/1aa7ba81bd4c083c12ef1aa2fee338f6cb60fa87/data/r2_compiled_anon.jsonl"
EXPERTQA_SHA256 = "b5e3070acb1f1df36bd404db720c3961ccd661531886ecc79d416da85d748ac5"


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Tokenify-analysis-resource-setup/1"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def _atomic_write(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(blob)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def download_verified(url: str, expected_sha256: str, destination: Path,
                      *, force: bool = False, fetch=None) -> dict:
    """Keep verified caches; never replace bytes with an unverified download."""
    destination = Path(destination)
    if destination.is_file() and not force:
        if _sha256(destination.read_bytes()) != expected_sha256:
            raise ValueError(f"Existing cache SHA-256 differs: {destination}; inspect it or use --force")
        return {"path": str(destination), "sha256": expected_sha256, "status": "cached"}
    blob = (fetch or _fetch)(url)
    if _sha256(blob) != expected_sha256:
        raise ValueError(f"Downloaded SHA-256 differs from pinned source: {url}")
    _atomic_write(destination, blob)
    return {"path": str(destination), "sha256": expected_sha256, "status": "downloaded"}


def extract_cmudict(blob: bytes, destination: Path, *, expected_files=None) -> None:
    """Validate the whole ZIP and its two fixed files before writing either."""
    expected_files = CMUDICT_FILES if expected_files is None else expected_files
    verified = {}
    seen = set()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                    or stat.S_ISLNK(mode)):
                raise ValueError(f"Unsafe ZIP member: {member.filename}")
            if member.is_dir():
                if member.filename != "cmudict/":
                    raise ValueError(f"Unexpected ZIP directory: {member.filename}")
                continue
            if member.filename not in expected_files or member.filename in seen:
                raise ValueError(f"Unexpected or duplicate ZIP member: {member.filename}")
            if member.file_size > 16 * 1024 * 1024:
                raise ValueError(f"ZIP member exceeds expected resource size: {member.filename}")
            seen.add(member.filename)
            content = archive.read(member)
            if _sha256(content) != expected_files[member.filename]:
                raise ValueError(f"ZIP member SHA-256 differs: {member.filename}")
            verified[member.filename] = content
    if set(verified) != set(expected_files):
        raise ValueError("CMUdict ZIP is missing required members")
    for name, content in verified.items():
        filename = "LICENSE.txt" if name == "cmudict/README" else "cmudict"
        _atomic_write(Path(destination) / filename, content)


def prepare_resources(support_dir: Path = DEFAULT_SUPPORT, *, force: bool = False) -> dict:
    support_dir = Path(support_dir)
    corpus_dir = support_dir / "nltk_data/corpora/cmudict"
    corpus_targets = {"cmudict/cmudict": corpus_dir / "cmudict", "cmudict/README": corpus_dir / "LICENSE.txt"}
    for member, target in corpus_targets.items():
        if target.exists() and _sha256(target.read_bytes()) != CMUDICT_FILES[member] and not force:
            raise ValueError(f"Existing CMUdict SHA-256 differs: {target}; inspect it or use --force")
    cached = all(path.is_file() and _sha256(path.read_bytes()) == CMUDICT_FILES[name]
                 for name, path in corpus_targets.items())
    if not cached or force:
        with tempfile.TemporaryDirectory(prefix="tokenify-resource-") as tmp:
            archive = Path(tmp) / "cmudict.zip"
            download_verified(CMUDICT_URL, CMUDICT_ARCHIVE_SHA256, archive)
            extract_cmudict(archive.read_bytes(), corpus_dir)
    expertqa = download_verified(EXPERTQA_URL, EXPERTQA_SHA256,
                                 support_dir / "expertqa/r2_compiled_anon.jsonl", force=force)
    return {
        "cmudict": {"path": str(corpus_dir), "status": "cached" if cached and not force else "downloaded",
                    "archive_sha256": CMUDICT_ARCHIVE_SHA256, "dictionary_sha256": CMUDICT_FILES["cmudict/cmudict"]},
        "expertqa": expertqa,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support-dir", type=Path, default=DEFAULT_SUPPORT)
    parser.add_argument("--force", action="store_true", help="Replace only the pinned cache files, still checking all hashes.")
    args = parser.parse_args()
    print(json.dumps(prepare_resources(args.support_dir, force=args.force), indent=2))


if __name__ == "__main__":
    main()
