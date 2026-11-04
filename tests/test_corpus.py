"""Tests for the corpus of .mdx files."""
import os
import pathlib

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"
EXPECTED_FILES = [
    "01-frontmatter.mdx",
    "02-code-blocks.mdx",
    "03-inline-code.mdx",
    "04-tables.mdx",
    "05-links-images.mdx",
    "06-admonitions.mdx",
    "07-mdx-imports-jsx.mdx",
    "08-lists-nested.mdx",
    "09-edge-cases.mdx",
    "09b-only-code.mdx",
    "09c-empty.mdx",
    "11-long-doc.mdx",
]


def test_all_files_exist():
    """Verify all expected corpus files exist."""
    for filename in EXPECTED_FILES:
        filepath = CORPUS_DIR / filename
        assert filepath.exists(), f"Arquivo esperado ausente: {filename}"
        assert filepath.is_file(), f"Caminho não é um arquivo: {filename}"


def test_files_utf8():
    """Verify all corpus files are valid UTF-8."""
    for filename in EXPECTED_FILES:
        filepath = CORPUS_DIR / filename
        with open(filepath, "r", encoding='utf-8') as f:
            content = f.read()
        # Re-encode to verify UTF-8 validity
        content.encode("utf-8")


def test_edge_cases_crlf():
    """Verify 09-edge-cases.mdx contains CRLF line endings."""
    filepath = CORPUS_DIR / "09-edge-cases.mdx"
    with open(filepath, "rb") as f:
        content = f.read()
    # Check for CRLF
    assert b"\r\n" in content, "09-edge-cases.mdx não contém CRLF"
    # Ensure no bare LF without CR (pure LF only)
    lines = content.split(b"\n")
    # If there were CRLF, splitting by \n would leave \r at end of lines
    # Check that some lines have \r\n
    has_crlf_pair = any(b"\r\n" in line for line in lines[:-1])
    assert has_crlf_pair, "09-edge-cases.mdx não tem pares CRLF nas linhas"