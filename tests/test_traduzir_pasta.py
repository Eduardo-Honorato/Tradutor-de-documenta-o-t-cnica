"""Tradutor de pasta (scripts/traduzir.py), sem rede."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import traduzir  # noqa: E402

import src.translate as T  # noqa: E402


def _monta(base):
    (base / "sub").mkdir(parents=True)
    (base / "node_modules").mkdir()
    (base / "out").mkdir()
    (base / "a.md").write_text("# Hello\n\nUse `x` now.\n", encoding="utf-8")
    (base / "sub" / "b.mdx").write_text("# World\n\nSome text here.\n", encoding="utf-8")
    (base / "node_modules" / "x.md").write_text("# Skip\n", encoding="utf-8")
    (base / "out" / "y.md").write_text("# Skip too\n", encoding="utf-8")
    (base / "c.txt").write_text("not markdown\n", encoding="utf-8")


def test_traduz_pasta_espelhando_subpastas(tmp_path, monkeypatch):
    entrada = tmp_path / "docs"
    _monta(entrada)
    saida = tmp_path / "saida"
    monkeypatch.setattr(T, "translate_with_retry", lambda t, *a, **k: t)
    antes = (entrada / "a.md").read_bytes()

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 0
    assert (saida / "a.md").exists()
    assert (saida / "sub" / "b.mdx").exists()
    assert not list(saida.rglob("x.md"))
    assert not list(saida.rglob("y.md"))
    assert not list(saida.rglob("c.txt"))
    assert (entrada / "a.md").read_bytes() == antes


def test_excecao_em_um_arquivo_nao_derruba_a_pasta(tmp_path, monkeypatch):
    entrada = tmp_path / "docs"
    _monta(entrada)
    saida = tmp_path / "saida"
    real = T.translate_file

    def quebra(texto, *a, **k):
        if "World" in texto:
            raise AssertionError("falha simulada")
        return real(texto, *a, **k)

    monkeypatch.setattr(T, "translate_file", quebra)
    monkeypatch.setattr(T, "translate_with_retry", lambda t, *a, **k: t)

    codigo = traduzir.main([str(entrada), "--saida", str(saida), "--no-cache"])

    assert codigo == 1
    assert (saida / "a.md").exists()
    assert not (saida / "sub" / "b.mdx").exists()
