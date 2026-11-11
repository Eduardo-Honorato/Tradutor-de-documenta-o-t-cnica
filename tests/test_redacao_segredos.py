"""Redacao de segredos: testes hermeticos (sem rede, sem .env). So chaves FALSAS."""
import importlib.util
import runpy
import sys
from pathlib import Path

from src import translate as T
from src.translate_client import redact_secrets

RAIZ = Path(__file__).resolve().parent.parent

# Chave falsa no formato nvapi- (70 caracteres).
FAKE_NVAPI = "nvapi-" + "A" * 64
# Chave falsa SEM o prefixo nvapi- (70 caracteres): so o valor exato vindo do
# ambiente a pega. Prova que a chave e lida a cada chamada.
FAKE_PLAIN = "segredo-teste-" + "Z" * 56


def _tem_trecho(texto, chave, n=12):
    return any(chave[i:i + n] in texto for i in range(len(chave) - n + 1))


def _sem_vazamento(texto, *chaves):
    for chave in chaves:
        assert chave not in texto
        assert not _tem_trecho(texto, chave)


def _carregar_script(nome):
    caminho = RAIZ / "scripts" / nome
    spec = importlib.util.spec_from_file_location("sob_teste_" + caminho.stem, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_valor_exato_da_chave_e_redigido_mesmo_sem_prefixo(monkeypatch):
    monkeypatch.setenv("TRANSLATOR_API_KEY", FAKE_PLAIN)
    saida = redact_secrets("falha ao chamar a API com " + FAKE_PLAIN + " e fim")
    _sem_vazamento(saida, FAKE_PLAIN)
    assert "***" in saida


def test_padrao_nvapi_e_redigido(monkeypatch):
    monkeypatch.delenv("TRANSLATOR_API_KEY", raising=False)
    saida = redact_secrets("erro: " + FAKE_NVAPI + " fim")
    _sem_vazamento(saida, FAKE_NVAPI)
    assert "nvapi-" not in saida
    assert "***" in saida


def test_padrao_bearer_e_redigido():
    saida = redact_secrets("Authorization: Bearer abc.DEF-123_xyz")
    assert "abc.DEF-123_xyz" not in saida
    assert "***" in saida


def test_aceita_entrada_que_nao_e_texto(monkeypatch):
    monkeypatch.setenv("TRANSLATOR_API_KEY", FAKE_PLAIN)

    class Relatorio:
        def __str__(self):
            return "relatorio com " + FAKE_PLAIN

    saida = redact_secrets(Relatorio())
    _sem_vazamento(saida, FAKE_PLAIN)


def test_resumo_do_tradutor_de_pasta_nao_vaza_a_chave(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRANSLATOR_API_KEY", FAKE_NVAPI)

    def quebra(*args, **kwargs):
        raise Exception("falha de rede com a chave " + FAKE_NVAPI)

    monkeypatch.setattr(T, "translate_file", quebra)

    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "a.mdx").write_text("# Ola\n\nTexto simples.\n", encoding="utf-8")
    saida = tmp_path / "saida"

    script = _carregar_script("traduzir.py")
    try:
        script.main([str(entrada), "--saida", str(saida)])
    except SystemExit:
        pass

    capturado = capsys.readouterr()
    texto = capturado.out + capturado.err
    _sem_vazamento(texto, FAKE_NVAPI)
    assert "***" in texto


def test_verificar_nao_imprime_a_chave(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRANSLATOR_API_KEY", FAKE_NVAPI)
    texto_doc = "# Titulo\n\nTexto com a chave " + FAKE_NVAPI + " no meio.\n"
    arquivo = tmp_path / "doc.mdx"
    arquivo.write_text(texto_doc, encoding="utf-8")

    class Relatorio:
        def format(self):
            return "relatorio com " + FAKE_NVAPI

    def falso(caminho, *args, **kwargs):
        return texto_doc, Relatorio(), True

    monkeypatch.setattr(T, "translate_file_path", falso)
    monkeypatch.setattr(sys, "argv", ["verificar.py", str(arquivo)])
    try:
        runpy.run_path(str(RAIZ / "scripts" / "verificar.py"), run_name="__main__")
    except SystemExit:
        pass

    capturado = capsys.readouterr()
    texto = capturado.out + capturado.err
    _sem_vazamento(texto, FAKE_NVAPI)
    assert "***" in texto

# ---------------------------------------------------------------------------
# Ordem da redacao dentro de translate_text: redigir ANTES de truncar em 120.
# Chave falsa SEM prefixo nvapi-: so o valor exato a redige, entao um pedaco
# cortado dela nao seria redigido se o corte viesse antes.
# ---------------------------------------------------------------------------
import pytest


def _preparar_ambiente(monkeypatch):
    import time
    import src.translate_client as C

    monkeypatch.setenv("TRANSLATOR_API_KEY", FAKE_PLAIN)
    monkeypatch.setenv("TRANSLATOR_BASE_URL", "https://exemplo.invalid/v1")
    monkeypatch.setenv("TRANSLATOR_MODEL", "modelo-teste")
    monkeypatch.setattr(C, "_REAL_API_KEY", FAKE_PLAIN)
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


def _chamar_traducao(monkeypatch, falso_post):
    import requests
    from src.translate import translate_with_retry

    monkeypatch.setattr(requests, "post", falso_post)
    return translate_with_retry("Hello world", max_retries=0)


@pytest.mark.parametrize("pos", [10, 100])
def test_excecao_com_chave_e_redigida_antes_do_corte(monkeypatch, capsys, pos):
    _preparar_ambiente(monkeypatch)

    def falso_post(*args, **kwargs):
        raise Exception("a" * pos + FAKE_PLAIN)

    _chamar_traducao(monkeypatch, falso_post)
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "[translate_client]" in texto
    _sem_vazamento(texto, FAKE_PLAIN)
    if pos == 10:
        assert "***" in texto


@pytest.mark.parametrize("pos", [10, 100])
def test_corpo_http_com_chave_e_redigido_antes_do_corte(monkeypatch, capsys, pos):
    _preparar_ambiente(monkeypatch)
    corpo = "a" * pos + FAKE_PLAIN

    class RespostaInvalida:
        # Status 200 com corpo que nao e JSON: o erro acontece DEPOIS de ler o
        # status, que e o unico caminho que imprime o preview do corpo.
        status_code = 200
        text = corpo

        def json(self):
            raise ValueError("corpo invalido")

    def falso_post(*args, **kwargs):
        return RespostaInvalida()

    _chamar_traducao(monkeypatch, falso_post)
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "Erro HTTP 200" in texto
    _sem_vazamento(texto, FAKE_PLAIN)
    if pos == 10:
        assert "***" in texto