"""Testes da Etapa 4: validadores de traducao.

Dois tipos de teste:
  * "nao reprove o que esta certo": traducoes validas (identidade, MAIUSCULAS,
    palavras com sufixo, CRLF) nunca podem gerar erro, em TODOS os arquivos do
    corpus. Isso protege contra falsos positivos.
  * "pegue o que esta errado": corrupcoes deliberadas (placeholder removido,
    link quebrado, tabela com coluna a mais, YAML invalido...) precisam gerar o
    erro certo e apontar a unidade certa.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.protect import protect  # noqa: E402
from src.validate import (  # noqa: E402
    normalize_translation,
    validate_document,
    validate_translation,
)

CORPUS_DIR = Path(__file__).parent / "corpus"
CORPUS_FILES = sorted(CORPUS_DIR.glob("*.mdx"))


def _name(path):
    return path.name


def _identity(doc):
    return {i: u["text"] for i, u in enumerate(doc.translatable_units)}


def _ph_re(doc):
    return re.compile("\u27e6" + re.escape(doc.prefix) + r"\d+\u27e7")


def _map_outside_placeholders(doc, text, fn):
    """Aplica fn so ao texto fora dos placeholders (como um tradutor bem comportado)."""
    parts = re.split("(" + _ph_re(doc).pattern + ")", text)
    return "".join(p if _ph_re(doc).fullmatch(p) else fn(p) for p in parts)


def _ph_for(doc, original_text):
    return next(k for k, v in doc.placeholder_map.items() if v == original_text)


def _codes(report, severity="error"):
    return report.codes(severity)


# ---------------------------------------------------------------------------
# Nao reprovar o que esta certo (corpus inteiro)
# ---------------------------------------------------------------------------

def test_corpus_is_present():
    assert len(CORPUS_FILES) >= 13


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_identity_translation_is_valid(filepath):
    text = filepath.read_bytes().decode("utf-8")
    doc = protect(text)
    report = validate_translation(text, _identity(doc), doc)
    assert report.ok, report.format()
    assert report.final_text == text


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_uppercase_translation_is_valid(filepath):
    text = filepath.read_bytes().decode("utf-8")
    doc = protect(text)
    translations = {i: _map_outside_placeholders(doc, t, str.upper) for i, t in _identity(doc).items()}
    report = validate_translation(text, translations, doc)
    assert report.ok, report.format()
       
    # Unidade que ja estava em MAIUSCULAS (ou sem letras minusculas) continua igual
    # ao original: o aviso "unchanged" e legitimo. Qualquer outro aviso nao e.
    identity = _identity(doc)
    same = {i for i, t in translations.items() if t == identity[i]}
    for w in report.warnings:
        assert w.code == "unchanged" and set(w.units) <= same, report.format()


@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_word_suffix_translation_is_valid(filepath):
    """Muda o tamanho e acrescenta acentos, como uma traducao de verdade faria."""
    text = filepath.read_bytes().decode("utf-8")
    doc = protect(text)
    add_suffix = lambda s: re.sub(r"[A-Za-z]{3,}", lambda m: m.group(0) + "\u00e7\u00e3o", s)  # noqa: E731
    translations = {i: _map_outside_placeholders(doc, t, add_suffix) for i, t in _identity(doc).items()}
    report = validate_translation(text, translations, doc)
    assert report.ok, report.format()


def test_crlf_document_accepts_lf_translations_and_keeps_crlf():
    path = CORPUS_DIR / "09-edge-cases.mdx"
    text = path.read_bytes().decode("utf-8")
    assert "\r\n" in text
    doc = protect(text)
    translations = {i: t.replace("\r\n", "\n") for i, t in _identity(doc).items()}
    report = validate_translation(text, translations, doc)
    assert report.ok, report.format()
    assert report.final_text == text
    assert "\n" not in report.final_text.replace("\r\n", "")


# ---------------------------------------------------------------------------
# Pegar o que esta errado (corpus)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filepath", CORPUS_FILES, ids=_name)
def test_removing_a_placeholder_is_detected_and_pointed_at_the_unit(filepath):
    text = filepath.read_bytes().decode("utf-8")
    doc = protect(text)
    ph_re = _ph_re(doc)
    candidates = [i for i, u in enumerate(doc.translatable_units) if ph_re.search(u["text"])]
    if not candidates:
        pytest.skip("arquivo sem unidades com placeholder")
    for idx in candidates[:10]:
        translations = _identity(doc)
        translations[idx] = ph_re.sub("", translations[idx], count=1)
        report = validate_translation(text, translations, doc)
        assert not report.ok, f"unidade {idx}: remocao de placeholder passou despercebida"
        assert idx in report.failed_units, report.format()
        assert "placeholder_missing" in _codes(report)


# ---------------------------------------------------------------------------
# Unidades: casos pequenos
# ---------------------------------------------------------------------------

ORIGINAL = "# Hello\n\nUse `x` and [link](https://a.b) now.\n"


def _setup():
    doc = protect(ORIGINAL)
    ph_code = _ph_for(doc, "`x`")
    ph_url = _ph_for(doc, "https://a.b")
    good = {0: "Ola", 1: f"Use {ph_code} e [link]({ph_url}) agora."}
    return doc, ph_code, ph_url, good


def test_a_correct_translation_passes():
    doc, _, _, good = _setup()
    report = validate_translation(ORIGINAL, good, doc)
    assert report.ok, report.format()
    assert report.final_text == "# Ola\n\nUse `x` e [link](https://a.b) agora.\n"


def test_missing_placeholder():
    doc, ph_code, ph_url, good = _setup()
    good[1] = f"Use e [link]({ph_url}) agora."
    report = validate_translation(ORIGINAL, good, doc)
    assert "placeholder_missing" in _codes(report)
    assert report.failed_units == [1]


def test_duplicated_placeholder():
    doc, ph_code, ph_url, good = _setup()
    good[1] = f"Use {ph_code} {ph_code} e [link]({ph_url}) agora."
    assert "placeholder_duplicated" in _codes(validate_translation(ORIGINAL, good, doc))


def test_invented_placeholder_fails_unit_and_restore():
    doc, ph_code, ph_url, good = _setup()
    good[1] = good[1] + f" \u27e6{doc.prefix}99\u27e7"
    report = validate_translation(ORIGINAL, good, doc)
    assert "placeholder_unknown" in _codes(report)
    assert "restore_failed" in _codes(report)
    assert report.final_text is None


def test_empty_translation():
    doc, _, _, good = _setup()
    good[0] = "   "
    report = validate_translation(ORIGINAL, good, doc)
    assert "empty_translation" in _codes(report)
    assert 0 in report.failed_units


def test_missing_unit_translation():
    doc, _, _, good = _setup()
    del good[1]
    report = validate_translation(ORIGINAL, good, doc)
    assert "missing_translation" in _codes(report)
    assert report.failed_units == [1]


def test_unknown_unit_index():
    doc, _, _, good = _setup()
    good[7] = "x"
    assert "unknown_unit" in _codes(validate_translation(ORIGINAL, good, doc))


def test_newline_added_to_heading():
    doc, _, _, good = _setup()
    good[0] = "Ola\n\nmundo"
    report = validate_translation(ORIGINAL, good, doc)
    assert "newline_added" in _codes(report)
    assert 0 in report.failed_units


def test_blank_line_inside_paragraph():
    text = "Linha um\nlinha dois.\n"
    doc = protect(text)
    report = validate_translation(text, {0: "Linha um\n\nlinha dois."}, doc)
    assert "blank_line_added" in _codes(report)


def test_new_braces_and_angle_brackets_are_errors():
    doc, _, _, good = _setup()
    for bad in ("Ola {x}", "Ola <b>", "Ola }"):
        trial = dict(good)
        trial[0] = bad
        report = validate_translation(ORIGINAL, trial, doc)
        assert "mdx_special_char" in _codes(report), bad


def test_link_syntax_must_be_kept():
    doc, ph_code, ph_url, good = _setup()
    good[1] = f"Use {ph_code} e [link] ({ph_url}) agora."
    assert "link_syntax_broken" in _codes(validate_translation(ORIGINAL, good, doc))


def test_block_marker_added():
    doc, ph_code, ph_url, good = _setup()
    good[1] = f"- Use {ph_code} e [link]({ph_url}) agora."
    assert "block_marker_added" in _codes(validate_translation(ORIGINAL, good, doc))


def test_pipe_in_table_cell_is_an_error():
    text = "| A | B |\n| --- | --- |\n| x y | z w |\n"
    doc = protect(text)
    translations = _identity(doc)
    translations[0] = "A | C"
    report = validate_translation(text, translations, doc)
    assert "pipe_in_cell" in _codes(report)
    assert "table_shape_changed" in _codes(report)
    assert 0 in report.failed_units


def test_unchanged_translation_is_only_a_warning():
    text = "Este texto nao foi traduzido de verdade.\n"
    doc = protect(text)
    report = validate_translation(text, _identity(doc), doc)
    assert report.ok
    assert "unchanged" in _codes(report, "warning")


def test_suspicious_length_is_only_a_warning():
    text = "Uma frase de tamanho razoavel para comparar depois.\n"
    doc = protect(text)
    report = validate_translation(text, {0: "Ok."}, doc)
    assert report.ok
    assert "length_ratio" in _codes(report, "warning")


def test_normalize_translation():
    assert normalize_translation("a\nb", "CRLF") == "a\r\nb"
    assert normalize_translation("a\r\nb", "CRLF") == "a\r\nb"
    assert normalize_translation("a\r\nb", "LF") == "a\nb"
    assert normalize_translation("a\r\nb\n", "misto") == "a\r\nb\n"


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------

FM = '---\ntitle: "Hello"\nslug: "hi"\n---\nBody text.\n'


def test_front_matter_unescaped_quote_is_an_error():
    doc = protect(FM)
    report = validate_translation(FM, {0: 'Diz "oi"', 1: "Corpo."}, doc)
    assert "yaml_value_unsafe" in _codes(report)
    assert "front_matter_invalid" in _codes(report)
    assert 0 in report.failed_units


def test_front_matter_escaped_quote_is_fine():
    doc = protect(FM)
    report = validate_translation(FM, {0: 'Diz \\"oi\\"', 1: "Corpo."}, doc)
    assert report.ok, report.format()


def test_front_matter_single_quotes_need_doubling():
    text = "---\ntitle: 'Hello'\n---\nBody text.\n"
    doc = protect(text)
    assert "yaml_value_unsafe" in _codes(validate_translation(text, {0: "It's", 1: "Corpo."}, doc))
    assert validate_translation(text, {0: "It''s", 1: "Corpo."}, doc).ok


def test_front_matter_unquoted_colon_space_is_an_error():
    text = "---\ntitle: Hello\n---\nBody text.\n"
    doc = protect(text)
    assert "yaml_value_unsafe" in _codes(validate_translation(text, {0: "Titulo: novo", 1: "Corpo."}, doc))
    assert validate_translation(text, {0: "Titulo novo", 1: "Corpo."}, doc).ok


# ---------------------------------------------------------------------------
# Documento: comparacao original x final
# ---------------------------------------------------------------------------

def _doc_codes(original, final):
    return [i.code for i in validate_document(original, final)]


def test_document_identical_has_no_issues():
    text = "# A {#a}\n\nTexto com `x` e [l](https://a.b).\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
    assert _doc_codes(text, text) == []


def test_heading_level_change():
    assert "headings_changed" in _doc_codes("# A\n\ntext\n", "## A\n\ntext\n")


def test_heading_removed():
    assert "headings_changed" in _doc_codes("# A\n\n## B\n\ntext\n", "# A\n\ntext\n")


def test_lost_explicit_anchor():
    assert "anchor_lost" in _doc_codes("## Install {#install}\n\ntext\n", "## Instalacao\n\ntext\n")


def test_internal_link_to_a_renamed_heading_is_broken():
    original = "## Install\n\nSee [x](#install).\n"
    assert "broken_anchor" in _doc_codes(original, "## Instalacao\n\nSee [x](#install).\n")
    assert _doc_codes(original, "## Instalacao {#install}\n\nSee [x](#install).\n") == []


def test_url_changed():
    assert "protected_changed" in _doc_codes("See [x](https://a.b).\n", "See [x](https://c.d).\n")


def test_new_code_block_appearing():
    assert "protected_changed" in _doc_codes("Text.\n", "Text.\n\n```js\nx\n```\n")


def test_code_block_edited():
    original = "```js\nlet a = 1;\n```\n"
    assert "protected_changed" in _doc_codes(original, "```js\nlet a = 2;\n```\n")


def test_inline_code_translated():
    assert "protected_changed" in _doc_codes("Use `build` now.\n", "Use `construir` agora.\n")


def test_jsx_tag_prop_translated():
    original = '<Callout title="Heads up" />\n'
    assert "protected_changed" in _doc_codes(original, '<Callout title="Atencao" />\n')


def test_added_heading_anchor_is_allowed():
    original = "## Install\n\ntext\n"
    final = "## Instalacao {#install}\n\ntext\n"
    assert "protected_changed" not in _doc_codes(original, final)


def test_table_gets_an_extra_column():
    original = "| A | B |\n| - | - |\n| 1 | 2 |\n"
    final = "| A | B | C |\n| - | - | - |\n| 1 | 2 | 3 |\n"
    assert "table_shape_changed" in _doc_codes(original, final)


def test_line_endings_normalized_by_the_translator():
    assert "line_endings_changed" in _doc_codes("a\r\n\r\nb\r\n", "a\n\nb\n")


def test_paragraph_split_changes_block_structure():
    assert "block_structure_changed" in _doc_codes("Linha um\nlinha dois.\n", "Linha um\n\nlinha dois.\n")


def test_list_turned_into_paragraph():
    assert "block_structure_changed" in _doc_codes("- um\n- dois\n", "um\n\ndois\n")


def test_front_matter_yaml_broken_in_final_document():
    original = '---\ntitle: "A"\nslug: "s"\n---\nx\n'
    final = '---\ntitle: "A "b""\nslug: "s"\n---\nx\n'
    codes = _doc_codes(original, final)
    assert "front_matter_invalid" in codes


def test_front_matter_other_field_changed():
    original = '---\ntitle: "A"\nslug: "s"\n---\nx\n'
    final = '---\ntitle: "B"\nslug: "outro"\n---\nx\n'
    assert "front_matter_invalid" in _doc_codes(original, final)


def test_report_format_and_failed_units():
    doc, ph_code, ph_url, good = _setup()
    good[1] = f"Use e [link]({ph_url}) agora."
    report = validate_translation(ORIGINAL, good, doc)
    text = report.format()
    assert "placeholder_missing" in text and "unidade 1" in text
    assert report.failed_units == [1]
    ok = validate_translation(ORIGINAL, _setup()[3], doc)
    assert ok.format().startswith("OK")
