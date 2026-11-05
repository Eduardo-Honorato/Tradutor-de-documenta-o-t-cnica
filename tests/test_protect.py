"""Testes da Etapa 3: protecao com placeholders e ida e volta.

Tres camadas:
  1. Corpus (tests/corpus/*.mdx): um teste por arquivo para cada invariante,
     para que uma falha nunca esconda as outras.
  2. "Oraculos" independentes: o vazamento de codigo/URL/tag e conferido com
     regex proprias do teste, sem usar o parser, para o teste nao concordar com
     os mesmos erros do codigo.
  3. Casos pequenos escritos a mao para cada requisito.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.protect import (  # noqa: E402
    LEFT_GUILLEMET,
    RIGHT_GUILLEMET,
    apply_translations,
    detect_line_endings,
    protect,
    restore,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
CORPUS_FILES = sorted(CORPUS_DIR.glob("*.mdx"))


def _name(path):
    return path.name


def _placeholders_in(text, prefix):
    pattern = re.escape(LEFT_GUILLEMET) + re.escape(prefix) + r"\d+" + re.escape(RIGHT_GUILLEMET)
    return re.findall(pattern, text)


def _unit_texts(doc):
    return [u["text"] for u in doc.translatable_units]


def _units_of(doc, kind):
    return [u["text"] for u in doc.translatable_units if u["type"] == kind]


# ---------------------------------------------------------------------------
# Oraculos independentes (nao usam o parser)
# ---------------------------------------------------------------------------

def _fenced_blocks(text):
    """Blocos cercados achados por varredura de linhas simples."""
    blocks = []
    pos = 0
    open_ = None  # (inicio, caractere, tamanho)
    for line in text.split("\n"):
        content = line.rstrip("\r")
        end = pos + len(content)
        stripped = content.lstrip(" \t")
        m = re.match(r"(`{3,}|~{3,})(.*)$", stripped)
        if open_ is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                open_ = (pos, m.group(1)[0], len(m.group(1)))
        elif m and m.group(1)[0] == open_[1] and len(m.group(1)) >= open_[2] and m.group(2).strip() == "":
            blocks.append((open_[0], end))
            open_ = None
        pos += len(line) + 1
    if open_ is not None:
        blocks.append((open_[0], len(text.rstrip("\r\n"))))
    return blocks


def _without_fenced(text):
    out = list(text)
    for s, e in _fenced_blocks(text):
        for i in range(s, min(e, len(out))):
            if out[i] not in "\r\n":
                out[i] = " "
    return "".join(out)


_TAG_ORACLE = re.compile(r"</?[A-Za-z][\w.:-]*(?:\s[^<>]*)?/?>")
_URL_ORACLE = re.compile(r"https?://[^\s<>)\]\"'`]+")
_INLINE_ORACLE = re.compile(r"(?<![`\\])`(?!\s)([^`\n]+?)(?<!\s)`(?!`)")
_IMPORT_ORACLE = re.compile(r"^import\s[^\n]*\sfrom\s+['\"][^'\"\n]+['\"];?", re.M)


# ---------------------------------------------------------------------------
# 1. Corpus
# ---------------------------------------------------------------------------

def test_corpus_is_present():
    assert len(CORPUS_FILES) >= 13, "o corpus da Etapa 2 deveria ter pelo menos 13 arquivos .mdx"


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_corpus_roundtrip_bytes(filepath):
    original = filepath.read_bytes()
    doc = protect(original.decode("utf-8"))
    restored = restore(doc).encode("utf-8")
    assert restored == original, (
        f"ida e volta falhou em {filepath.name}: {len(original)} bytes != {len(restored)} bytes"
    )


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_corpus_placeholders_consistent(filepath):
    doc = protect(filepath.read_bytes().decode("utf-8"))
    found = _placeholders_in(doc.protected_text, doc.prefix)
    assert sorted(found) == sorted(doc.placeholder_map), "placeholders no texto != mapa"
    assert len(found) == len(set(found)), "algum placeholder aparece mais de uma vez"


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_corpus_no_protected_content_leaks(filepath):
    text = filepath.read_bytes().decode("utf-8")
    doc = protect(text)
    ptext = doc.protected_text

    for s, e in _fenced_blocks(text):
        assert text[s:e] not in ptext, f"bloco de codigo vazou: {text[s:e][:60]!r}"

    prose = _without_fenced(text)
    for m in _INLINE_ORACLE.finditer(prose):
        assert m.group(0) not in ptext, f"codigo inline vazou: {m.group(0)!r}"

    no_inline = _INLINE_ORACLE.sub("", prose)
    for m in _TAG_ORACLE.finditer(no_inline):
        assert m.group(0) not in ptext, f"tag vazou: {m.group(0)!r}"
    for m in _URL_ORACLE.finditer(no_inline):
        url = m.group(0).rstrip(".,;:!?*_")
        assert url not in ptext, f"URL vazou: {url!r}"
    for m in _IMPORT_ORACLE.finditer(no_inline):
        assert m.group(0) not in ptext, f"import vazou: {m.group(0)!r}"

    if text.startswith("---"):
        for m in re.finditer(r"^(?:slug|id|sidebar_position|tags)\s*:[^\n]*", text, re.M):
            assert m.group(0).rstrip("\r") not in ptext, f"campo de front matter vazou: {m.group(0)!r}"


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_corpus_units_are_well_formed(filepath):
    doc = protect(filepath.read_bytes().decode("utf-8"))
    last_end = -1
    for i, u in enumerate(doc.translatable_units):
        assert doc.protected_text[u["start"]:u["end"]] == u["text"]
        assert u["start"] >= last_end, "unidades se sobrepoem ou estao fora de ordem"
        assert u["position"] == i
        assert "\x00" not in u["text"]
        without_ph = re.sub(
            re.escape(LEFT_GUILLEMET) + re.escape(doc.prefix) + r"\d+" + re.escape(RIGHT_GUILLEMET),
            "",
            u["text"],
        )
        assert re.search(r"[^\W\d_]", without_ph), f"unidade sem letras: {u['text']!r}"
        last_end = u["end"]


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_corpus_identity_translation_roundtrip(filepath):
    original = filepath.read_bytes().decode("utf-8")
    doc = protect(original)
    same = {i: u["text"] for i, u in enumerate(doc.translatable_units)}
    assert restore(apply_translations(doc, same)) == original


# ---------------------------------------------------------------------------
# 3. Casos pequenos
# ---------------------------------------------------------------------------

def test_basic_prose_and_heading():
    doc = protect("# Teste\n\nTexto de teste.")
    assert _units_of(doc, "title") == ["Teste"]
    assert _units_of(doc, "prose") == ["Texto de teste."]
    assert doc.placeholder_map == {}


def test_link_url_protected_text_kept():
    text = "Visite [a documentacao](https://exemplo.com) para mais."
    doc = protect(text)
    assert "https://exemplo.com" not in doc.protected_text
    assert "[a documentacao](" in doc.protected_text
    assert any("a documentacao" in t for t in _unit_texts(doc))
    assert restore(doc) == text


def test_image_alt_kept_and_src_protected():
    text = "![Diagrama de arquitetura](./img/arch.png) e [t](u)"
    doc = protect(text)
    assert "./img/arch.png" not in doc.protected_text
    assert "Diagrama de arquitetura" in doc.protected_text
    assert restore(doc) == text


def test_link_and_image_do_not_overlap_or_grow():
    text = "[![Build](https://ci.example.com/b.svg)](https://ci.example.com)"
    doc = protect(text)
    assert restore(doc) == text
    assert len(doc.placeholder_map) == 2


def test_reference_links_keep_labels():
    text = "Veja [a API][api], o [guia][] e [api].\n\n[api]: https://example.com/api\n[guia]: ./g.md\n"
    doc = protect(text)
    restored = restore(doc)
    assert restored == text
    assert "[api]: https://example.com/api" not in doc.protected_text
    assert "https://example.com/api" not in doc.protected_text


def test_inline_code_single_and_double_backticks():
    text = "Use `funcao()` e ``a `b` c`` agora."
    doc = protect(text)
    assert doc.protected_text.count(LEFT_GUILLEMET) == 2
    assert "funcao()" not in doc.protected_text
    assert "`b`" not in doc.protected_text
    assert restore(doc) == text


def test_escaped_backtick_is_text():
    text = "Um acento grave escapado \\` aqui e \\`literal\\` entre crases escapadas."
    doc = protect(text)
    assert doc.placeholder_map == {}
    assert restore(doc) == text


def test_braces_inside_code_block_are_not_expressions():
    text = '```css title="print" {2}\n@media print {\n  .a { color: red; }\n}\n```\n\nDepois.\n'
    doc = protect(text)
    assert len(doc.placeholder_map) == 1
    assert "color" not in doc.protected_text
    assert restore(doc) == text


def test_fence_with_meta_and_four_backticks_and_tilde():
    text = (
        "````md\n```js\nlet a = 1;\n```\n````\n\n"
        "~~~python\nx = {1: 2}\n~~~\n\n"
        "```bash title=\"x\" {1,3}\nls\n```\n"
    )
    doc = protect(text)
    assert len(doc.placeholder_map) == 3
    assert "let a" not in doc.protected_text and "x = " not in doc.protected_text
    assert restore(doc) == text


def test_unclosed_fence_protects_until_end():
    text = "Antes.\n\n```js\nlet a = 1;\nlet b = 2;\n"
    doc = protect(text)
    assert "let a" not in doc.protected_text
    assert restore(doc) == text


def test_front_matter_only_title_and_description():
    text = '---\ntitle: "Guia rapido"\ndescription: "Configure e execute"\nslug: "quick-start"\nsidebar_position: 2\ntags: [a, b]\n---\n# Conteudo\nMais texto.'
    doc = protect(text)
    assert _units_of(doc, "title")[0] == "Guia rapido"
    assert _units_of(doc, "description") == ["Configure e execute"]
    for hidden in ("slug", "quick-start", "sidebar_position", "tags"):
        assert hidden not in doc.protected_text
    assert restore(doc) == text


def test_front_matter_unquoted_single_quoted_and_crlf():
    text = "---\r\ntitle: Guia sem aspas\r\ndescription: 'Com aspas simples'\r\nid: x\r\n---\r\n\r\nTexto.\r\n"
    doc = protect(text)
    assert _units_of(doc, "title") == ["Guia sem aspas"]
    assert _units_of(doc, "description") == ["Com aspas simples"]
    assert restore(doc) == text


def test_front_matter_translation_keeps_yaml_structure():
    text = '---\ntitle: "Hello"\nslug: "hi"\n---\nBody text here.\n'
    doc = protect(text)
    idx = next(i for i, u in enumerate(doc.translatable_units) if u["type"] == "title")
    out = restore(apply_translations(doc, {idx: "Ola"}))
    assert out == '---\ntitle: "Ola"\nslug: "hi"\n---\nBody text here.\n'


def test_import_is_code_but_prose_starting_with_import_is_not():
    text = "import Tabs from '@theme/Tabs';\nimport {\n  A,\n  B\n} from \"@x/y\";\n\nimport the data before starting.\n"
    doc = protect(text)
    assert _unit_texts(doc) == ["import the data before starting."]
    assert restore(doc) == text


def test_multiline_export_is_protected():
    text = 'export const meta = {\n  title: "Hi {there}",\n  tags: ["a"],\n};\n\nTexto.\n'
    doc = protect(text)
    assert "meta" not in doc.protected_text
    assert _unit_texts(doc) == ["Texto."]
    assert restore(doc) == text


def test_jsx_tags_protected_children_translatable():
    text = '<Tabs defaultValue="npm" values={[{label: "npm", value: "npm"}]}>\n<TabItem value="npm" label="npm">\n\nInstale com `npm`.\n\n</TabItem>\n</Tabs>\n'
    doc = protect(text)
    assert "TabItem" not in doc.protected_text and "defaultValue" not in doc.protected_text
    units = _unit_texts(doc)
    assert len(units) == 1
    assert re.fullmatch(
        r"Instale com " + re.escape(LEFT_GUILLEMET) + re.escape(doc.prefix) + r"\d+" + re.escape(RIGHT_GUILLEMET) + r"\.",
        units[0],
    )
    assert restore(doc) == text


def test_inline_tags_split_text_but_keep_it_translatable():
    text = "Press <kbd>Ctrl</kbd> + <kbd>C</kbd> to stop."
    doc = protect(text)
    assert "<kbd>" not in doc.protected_text
    assert "Ctrl" in doc.protected_text and "to stop" in doc.protected_text
    assert restore(doc) == text


def test_comparison_operators_are_not_tags():
    text = "Compare a < b and 1<2 and x > y here."
    doc = protect(text)
    assert doc.placeholder_map == {}
    assert restore(doc) == text


def test_expression_inside_tag_prop_is_one_placeholder():
    text = '<Card title={`a ${b}`} data={{x: 1}} />'
    doc = protect(text)
    assert len(doc.placeholder_map) == 1
    assert restore(doc) == text


def test_link_text_containing_inline_code():
    text = "Use [`acme init` command](/docs/init) now."
    doc = protect(text)
    assert "acme init" not in doc.protected_text
    assert "/docs/init" not in doc.protected_text
    assert "command](" in doc.protected_text
    assert restore(doc) == text


def test_expression_in_text_and_comments():
    text = "Versao {version} e {/* comentario */} e <!-- html -->.\n"
    doc = protect(text)
    assert "version" not in doc.protected_text
    assert "comentario" not in doc.protected_text
    assert "html" not in doc.protected_text
    assert restore(doc) == text


def test_bare_url_and_autolink_are_protected():
    text = "Veja https://example.com/x. Ou <https://example.org/y> e <a@b.org>."
    doc = protect(text)
    assert "example.com" not in doc.protected_text
    assert "example.org" not in doc.protected_text
    assert restore(doc) == text


def test_table_cells_are_units_and_code_with_pipe_is_safe():
    text = "| Opcao | Tipo |\n| --- | --- |\n| `--a` | `x \\| y` |\n| texto | mais texto |\n"
    doc = protect(text)
    cells = _units_of(doc, "table_cell")
    assert cells[:2] == ["Opcao", "Tipo"]
    assert "texto" in cells and "mais texto" in cells
    assert "x \\| y" not in doc.protected_text
    assert restore(doc) == text


def test_heading_anchor_protected_and_slug_recorded():
    text = "## Instalacao rapida {#install}\n\n## Outro titulo\n"
    doc = protect(text)
    assert "{#install}" not in doc.protected_text
    assert doc.heading_slugs[0]["explicit_id"] == "install"
    assert doc.heading_slugs[0]["text"] == "Instalacao rapida"
    assert doc.heading_slugs[1]["slug"] == "outro-titulo"
    assert restore(doc) == text


def test_admonition_markers_protected_text_translatable():
    text = ":::warning[Cuidado]\nNao rode em producao.\n:::\n"
    doc = protect(text)
    assert ":::" not in doc.protected_text
    assert "Nao rode em producao." in doc.protected_text
    assert restore(doc) == text


def test_indented_code_but_not_list_continuation():
    text = "Exemplo:\n\n    acme --help\n    acme run\n\nDepois.\n\n- item\n\n    continuacao do item\n"
    doc = protect(text)
    assert "acme --help" not in doc.protected_text
    assert "continuacao do item" in doc.protected_text
    assert restore(doc) == text


def test_list_quote_and_paragraph_units():
    text = "- primeiro item\n- [ ] tarefa\n\n> citacao aqui\n\nLinha um\nlinha dois.\n"
    doc = protect(text)
    assert _unit_texts(doc) == ["primeiro item", "tarefa", "citacao aqui", "Linha um\nlinha dois."]


def test_crlf_is_preserved_and_detected():
    text = "# T\r\n\r\nLinha `a` b\r\n\r\n```js\r\nlet x = {a:1};\r\n```\r\n"
    doc = protect(text)
    assert doc.line_endings == "CRLF"
    assert "let x" not in doc.protected_text
    assert restore(doc) == text


def test_detect_line_endings_variants():
    assert detect_line_endings("a\nb\n") == "LF"
    assert detect_line_endings("a\r\nb\r\n") == "CRLF"
    assert detect_line_endings("a\r\nb\n") == "misto"
    assert detect_line_endings("sem quebra") == "LF"
    assert detect_line_endings("") == "LF"


@pytest.mark.parametrize(
    "text",
    ["", "\n", "   ", "sem quebra final", "```", "`", "{", "<", "[a](", "---\n", "---\ntitle: x\n", "![](x)"],
    ids=repr,
)
def test_degenerate_inputs_roundtrip(text):
    assert restore(protect(text)) == text


def test_literal_placeholder_in_text_does_not_collide():
    text = "Texto com \u27e6P1\u27e7 literal e `codigo` aqui."
    doc = protect(text)
    assert doc.prefix != "P"
    assert restore(doc) == text
    assert "\u27e6P1\u27e7" in doc.protected_text  # o literal fica intacto


def test_restore_rejects_unknown_placeholder():
    doc = protect("Use `a` aqui.")
    doc.protected_text += f" {LEFT_GUILLEMET}{doc.prefix}99{RIGHT_GUILLEMET}"
    with pytest.raises(ValueError, match="desconhecido"):
        restore(doc)


def test_apply_translations_changes_only_the_units():
    text = "# Hello\n\nUse `x` now.\n"
    doc = protect(text)
    ph = next(iter(doc.placeholder_map))
    out = restore(apply_translations(doc, {0: "Ola", 1: f"Use {ph} agora."}))
    assert out == "# Ola\n\nUse `x` agora.\n"


def test_protect_does_not_modify_input_text_without_special_elements():
    text = "Apenas texto simples, sem nada especial.\n"
    doc = protect(text)
    assert doc.protected_text == text
