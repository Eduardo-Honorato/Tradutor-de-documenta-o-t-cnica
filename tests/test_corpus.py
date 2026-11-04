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
    "10-glossary-terms.mdx",
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
    """Verify 09-edge-cases.mdx has CRLF line endings in all lines."""
    filepath = CORPUS_DIR / "09-edge-cases.mdx"
    with open(filepath, "rb") as f:
        content = f.read()
    # Verificar que todos os pares de linha são CRLF
    lines = content.split(b'\r\n')
    # O último elemento pode ser vazio se o arquivo terminar com newline
    assert len(lines) > 1, "Arquivo está vazio ou tem apenas uma linha"
    # Contar LF soltos (não seguidos por CR)
    bare_lf_count = content.count(b'\n') - content.count(b'\r\n')
    assert bare_lf_count == 0, f"Encontrados {bare_lf_count} LF soltos em 09-edge-cases.mdx"
    # Verificação concluída: mais de uma linha após split por CRLF + nenhum LF solto
    # indica que todas as linhas têm CRLF consistente