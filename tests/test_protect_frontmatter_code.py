"""Regressao: codigo inline dentro de valor do front matter nao pode quebrar protect()."""
from src.protect import protect
from src.translate import translate_file

TEXTO = '---\ntitle: "Using the `fetch` helper"\ndescription: "Learn how to call the API."\n---\n\n# Using the `fetch` helper\n\nBody text here.\n'


def test_protect_frontmatter_title_with_inline_code():
    doc = protect(TEXTO)
    titulos = [u for u in doc.translatable_units if u["type"] == "title"]
    assert len(titulos) == 2
    assert "`" not in titulos[0]["text"]
    assert "\u27e6" in titulos[0]["text"]


def test_translate_file_frontmatter_title_with_inline_code(monkeypatch):
    def mock(t, max_retries=2):
        return (
            t.replace("Using the", "Usando o")
            .replace("Learn how to call the API.", "Aprenda a chamar a API.")
            .replace("Body text here.", "Texto do corpo.")
        )

    monkeypatch.setattr("src.translate.translate_with_retry", mock)
    final_text, report, success = translate_file(TEXTO, max_retries=0)
    assert success is True, report.format()
    assert '`fetch`' in final_text
    assert 'title: "Usando o `fetch` helper"' in final_text
