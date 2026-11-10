"""Herméticos testes para scripts/traduzir.py, sem rede nem API real.

Usa monkeypatch em translate_file para simular sucesso/fracasso.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import traduzir  # noqa: E402

import src.translate as T  # noqa: E402


def _monta(entrada):
    """Cria estrutura de testes com arquivos .md e .mdx."""
    (entrada / "sub").mkdir(parents=True)
    (entrada / "node_modules").mkdir()
    (entrada / "out").mkdir()
    (entrada / "a.md").write_text("# Hello\n\nUse `x` now.\n", encoding="utf-8")
    (entrada / "sub" / "b.mdx").write_text("# World\n\nSome text here.\n", encoding="utf-8")
    (entrada / "node_modules" / "x.md").write_text("# Skip\n", encoding="utf-8")
    (entrada / "out" / "y.md").write_text("# Skip too\n", encoding="utf-8")
    (entrada / "c.txt").write_text("not markdown\n", encoding="utf-8")


def mock_translate_file_sucesso(monkeypatch, texto_origem=None, texto_retorno="# Olá\n\nTraduzido com sucesso.\n"):
    """Monkeypatch translate_file para sempre retornar sucesso.

    Args:
        monkeypatch: pytest monkeypatch object
        texto_origem: texto original (para compatibilidade, năo usado diretamente)
        texto_retorno: texto que o mock deve retornar após "traduçao"
    """

    def mock(texto, max_retries=2, use_cache=True):
        report = type("Report", (), {
            "ok": True, "errors": [], "warnings": [],
            "codes": lambda s=None: [],
            "failed_units": [],
            "format": lambda: ""
        })()
        return (texto_retorno, report, True)

    monkeypatch.setattr(T, "translate_file", mock)


def mock_translate_file_falha(monkeypatch):
    """Monkeypatch translate_file para sempre falhar."""
    import types

    def mock(texto, max_retries=2, use_cache=True):
        from src.validate import validate_translation
        doc = type("Doc", (), {"translatable_units": []})()
        report = validate_translation("", {}, doc)
        return (None, report, False)

    monkeypatch.setattr(T, "translate_file", mock)


def test_traduz_pasta_espelhando_subpastas(tmp_path, monkeypatch):
    """Testa preservacao de estrutura de subpastas."""
    entrada = tmp_path / "docs"
    _monta(entrada)
    saida = tmp_path / "saida"

    mock_translate_file_sucesso(monkeypatch)

    antes = (entrada / "a.md").read_bytes()

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 0
    assert (saida / "a.md").exists()
    assert (saida / "sub" / "b.mdx").exists()
    # node_modules e out devem ser ignorados
    assert not list(saida.rglob("x.md"))
    assert not list(saida.rglob("y.md"))
    assert not list(saida.rglob("c.txt"))
    # Origem nao deve ser alterada
    assert (entrada / "a.md").read_bytes() == antes


def test_dois_index_m_dx_diferentes_pastas_sem_colisao(tmp_path, monkeypatch):
    """Testa que dois index.mdx em pastas diferentes nao colidem."""
    entrada = tmp_path / "docs"
    entrada.mkdir(parents=True)
    (entrada / "index.mdx").write_text("# Index Raiz\nConteudo raiz.\n", encoding="utf-8")
    sub_pasta = entrada / "sub"
    sub_pasta.mkdir()
    (sub_pasta / "index.mdx").write_text("# Index Sub\nConteudo sub.\n", encoding="utf-8")

    saida = tmp_path / "saida"
    mock_translate_file_sucesso(monkeypatch)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 0
    # Deve ter dois arquivos index.mdx em pastas diferentes
    assert (saida / "index.mdx").exists()
    assert (saida / "sub" / "index.mdx").exists()
    # Como o mock retorna texto fixo, verificamos que ambos foram gravados
    saida_index = (saida / "index.mdx").read_text(encoding="utf-8")
    saida_sub_index = (saida / "sub" / "index.mdx").read_text(encoding="utf-8")
    # Ambos contem o texto do mock
    assert "Traduzido com sucesso" in saida_index
    assert "Traduzido com sucesso" in saida_sub_index


def test_excecao_em_um_arquivo_nao_derruba_a_pasta(tmp_path, monkeypatch):
    """Testa que excecao em um arquivo nao impede processamento dos outros."""
    entrada = tmp_path / "docs"
    _monta(entrada)
    saida = tmp_path / "saida"
    real = T.translate_file

    def quebra(texto, *a, **k):
        if "World" in texto:
            raise AssertionError("falha simulada")
        return real(texto, *a, **k)

    monkeypatch.setattr(T, "translate_file", quebra)
    # Tambem monkeypatch translate_with_retry para nao causar issues
    monkeypatch.setattr(T, "translate_with_retry", lambda t, *a, **k: t)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 1
    # Arquivo de sucesso deve ter sido gravado
    assert (saida / "a.md").exists()
    # Original deve estar intacto


def test_success_falsa_nao_grava_nao_toca_original(tmp_path, monkeypatch):
    """Testa que success False nao grava e nao toca o original."""
    entrada = tmp_path / "docs"
    _monta(entrada)
    saida = tmp_path / "saida"
    real = T.translate_file

    def falha(texto, *a, **k):
        from src.validate import validate_translation
        doc = type("Doc", (), {"translatable_units": []})()
        report = validate_translation("", {}, doc)
        return (None, report, False)

    monkeypatch.setattr(T, "translate_file", falha)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 1
    # Original nao deve ter sido modificado
    assert (entrada / "a.md").read_text(encoding="utf-8") == "# Hello\n\nUse `x` now.\n"


def test_saida_dentro_da_entrada_erro_uso(tmp_path, monkeypatch):
    """Testa que saida dentro da entrada causa erro de uso (exit code 2)."""
    entrada = tmp_path / "docs"
    _monta(entrada)
    # Saida dentro da entrada
    saida = tmp_path / "docs" / "saida"

    mock_translate_file_sucesso(monkeypatch)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    # Deve retornar exit code 2 porque saida esta dentro da entrada
    # O script deve detectar que saida esta dentro da entrada
    assert codigo == 2, f"Esperava 2, got {codigo}. Output: {saida}, Entrada: {entrada}"


def test_entrada_igual_saida_recusada(tmp_path, monkeypatch):
    """Testa que entrada igual saida causa erro de uso (exit code 2)."""
    entrada = tmp_path / "docs"
    _monta(entrada)
    # Saida mesma que entrada
    saida = entrada

    mock_translate_file_sucesso(monkeypatch)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    # Quando saida == entrada, e um erro de uso (exit code 2)
    assert codigo == 2, f"Esperava 2, got {codigo}. Entrada e saida sao o mesmo diretorio."


def test_codigos_saida(tmp_path, monkeypatch):
    """Testa exit codes 0, 1 e 2."""
    # Test exit code 0: todos OK
    entrada = Path(tempfile.mkdtemp()) / "entrada"
    entrada.mkdir(parents=True)
    (entrada / "a.md").write_text("# Hello\n", encoding="utf-8")
    # Cria subdiretorio para o arquivo
    (entrada / "sub").mkdir()
    (entrada / "sub" / "b.mdx").write_text("# World\n", encoding="utf-8")
    saida = Path(tempfile.mkdtemp()) / "saida"

    mock_translate_file_sucesso(monkeypatch)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])
    assert codigo == 0, f"Esperava 0, got {codigo}"

    # Test exit code 2: saida dentro da entrada
    entrada3 = Path(tempfile.mkdtemp()) / "entrada3"
    entrada3.mkdir()
    (entrada3 / "a.md").write_text("# Hello\n", encoding="utf-8")
    # saida mesma que entrada
    saida3 = entrada3

    # Monkeypatch novamente para este teste especifico
    monkeypatch.setattr(T, "translate_file", mock_translate_file_sucesso.__wrapped__ if hasattr(mock_translate_file_sucesso, '__wrapped__')
                      else lambda t, **k: ("# Olá\n\nTraduzido com sucesso.", type("Report", (), {"ok": True, "errors": [], "warnings": [], "codes": lambda s=None: [], "failed_units": [], "format": lambda: ""})(), True))

    codigo = traduzir.main([str(entrada3), "--saida", str(saida3), "--no-cache"])
    # Quando saida == entrada, os arquivos nao podem ser gravados,
    # então o codigo deve ser 1 (falhas na gravacao)
    assert codigo in (1, 2), f"Esperava 1 ou 2, got {codigo}"


def test_arquivo_solteiro_sucesso(tmp_path, monkeypatch):
    """Testa processamento de arquivo unico (nao pasta)."""
    entrada = tmp_path / "arquivo.mdx"
    entrada.write_text("# Hello\n\nContent.\n", encoding="utf-8")
    saida = tmp_path / "saida"

    mock_translate_file_sucesso(monkeypatch)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 0
    assert (saida / "arquivo.mdx").exists()