"""Traduz uma pasta (ou um arquivo) de documentacao .md/.mdx para PT-BR.

Uso:
  python scripts/traduzir.py <pasta_ou_arquivo> [--out PASTA] [--no-cache]
                             [--max-retries N] [--qualidade]

- Percorre .md e .mdx (ignora node_modules, out, .git e .translate_cache).
- Grava em <out>/<caminho relativo>; nunca altera a origem.
- Uma excecao em um arquivo vira falha no relatorio e o processo continua.
- Codigo de saida 1 se algum arquivo falhar.
- Codigo de saida 2 se saida for invalida (igual ou dentro da entrada).
"""
import argparse
import sys
import time
import os
from pathlib import Path

PROJETO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJETO))
sys.path.insert(0, str(PROJETO / "scripts"))

from src import translate as T  # noqa: E402
from src.protect import protect  # noqa: E402


IGNORAR = {"node_modules", "out", ".git", ".translate_cache"}
EXTENSOES = {".md", ".mdx"}


def validar_saida(alvo: Path, out_raiz: Path) -> int:
    """Validar diretorio de saida.

    Retorna exit code 2 se saida for igual ou estiver dentro do alvo.
    """
    alvo_resolvido = alvo.resolve()
    out_resolvido = out_raiz.resolve()

    # Se saida for o mesmo que o alvo
    try:
        if out_resolvido == alvo_resolvido:
            print(
                "[erro] Diretorio de saida nao pode ser o mesmo que o diretorio de entrada."
            )
            return 2
    except ValueError:
        pass

    # Se saida estiver dentro do alvo (saida dentro da entrada)
    try:
        if out_resolvido.is_relative_to(alvo_resolvido):
            print(
                "[erro] Diretorio de saida nao pode estar dentro do diretorio de entrada."
            )
            return 2
    except ValueError:
        pass

    return 0


def coletar(alvo, out_raiz):
    """Devolve (raiz, lista de arquivos .md/.mdx), podando as pastas ignoradas."""
    if alvo.is_file():
        return alvo.parent, [alvo]
    arquivos = []
    for pasta, subpastas, nomes in os.walk(alvo):
        subpastas[:] = sorted(
            d for d in subpastas
            if d not in IGNORAR and (Path(pasta) / d).resolve() != out_raiz
        )
        for nome in sorted(nomes):
            if Path(nome).suffix.lower() in EXTENSOES:
                arquivos.append(Path(pasta) / nome)
    return alvo, arquivos


def motivo_do_relatorio(relatorio):
    try:
        texto = relatorio.format()
    except Exception:
        texto = str(relatorio)
    erros = [l.strip() for l in texto.splitlines() if l.strip().lower().startswith("[error]")]
    if erros:
        return " | ".join(erros[:3])
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    return linhas[0] if linhas else "falha sem detalhe"


def traduzir_arquivo(caminho, raiz, out_raiz, max_retries, use_cache, qualidade):
    rel = caminho.relative_to(raiz)
    r = {"rel": str(rel), "ok": False, "motivo": "", "avisos": [],
         "unidades": None, "chamadas": 0, "tempo": 0.0}
    contador = {"n": 0}
    original_retry = T.translate_with_retry

    def contado(*args, **kwargs):
        contador["n"] += 1
        return original_retry(*args, **kwargs)

    t0 = time.time()
    T.translate_with_retry = contado
    try:
        texto = caminho.read_text(encoding="utf-8")
        try:
            r["unidades"] = len(protect(texto).translatable_units)
        except Exception:
            pass
        final, relatorio, ok = T.translate_file(
            texto, max_retries=max_retries, use_cache=use_cache
        )
        if not ok or not final:
            r["motivo"] = motivo_do_relatorio(relatorio)
        else:
            destino = T.write_translation_output(
                str(caminho), final, out_dir=(out_raiz / rel.parent).resolve()
            )
            if destino is None:
                r["motivo"] = "nao gravou a saida"
            else:
                r["ok"] = True
                if qualidade:
                    from checar_qualidade import checar
                    r["avisos"] = checar(texto, final)
    except Exception as e:
        r["motivo"] = f"{type(e).__name__}: {e}"
    finally:
        T.translate_with_retry = original_retry
        r["chamadas"] = contador["n"]
        r["tempo"] = time.time() - t0
    return r


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Traduz .md/.mdx de uma pasta para PT-BR.")
    ap.add_argument("alvo", help="pasta (ou arquivo) com a documentacao em ingles")
    ap.add_argument("--saida", default=str(PROJETO / "out"),
                    help="pasta de saida (padrao: out/ do projeto)")
    ap.add_argument("--no-cache", action="store_true", help="nao usa o cache de traducoes")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--qualidade", action="store_true",
                    help="roda checar_qualidade em cada arquivo traduzido")
    args = ap.parse_args(argv)

    alvo = Path(args.alvo).resolve()
    if not alvo.exists():
        print(f"caminho nao encontrado: {alvo}")
        return 2
    out_raiz = Path(args.saida).resolve()

    # Validar saida - saida nao pode ser igual ou estar dentro da entrada
    codigo_validacao = validar_saida(alvo, out_raiz)
    if codigo_validacao == 2:
        return 2

    raiz, arquivos = coletar(alvo, out_raiz)
    if not arquivos:
        print("nenhum .md/.mdx encontrado")
        return 0

    print(f"{len(arquivos)} arquivo(s) em {raiz} -> {out_raiz}", flush=True)
    inicio = time.time()
    resultados = []
    for i, caminho in enumerate(arquivos, 1):
        r = traduzir_arquivo(caminho, raiz, out_raiz, args.max_retries,
                             not args.no_cache, args.qualidade)
        resultados.append(r)
        marca = "ok   " if r["ok"] else "FALHA"
        print(f"[{i}/{len(arquivos)}] {marca} {r['rel']} "
              f"({r['chamadas']} chamadas, {r['tempo']:.1f}s)", flush=True)
        if not r["ok"]:
            print(f"        motivo: {r['motivo']}", flush=True)

    total = time.time() - inicio
    falhas = [r for r in resultados if not r["ok"]]
    unidades = sum(r["unidades"] or 0 for r in resultados)
    chamadas = sum(r["chamadas"] for r in resultados)
    print()
    print("=== resumo ===")
    print(f"arquivos: {len(resultados)} | traduzidos: {len(resultados) - len(falhas)} "
          f"| falhas: {len(falhas)} | tempo total: {total:.1f}s")
    print(f"unidades: {unidades} | chamadas a API: {chamadas} "
          f"| sem chamada (cache ou titulos repetidos, aprox.): {max(0, unidades - chamadas)}")
    for r in resultados:
        for aviso in r["avisos"]:
            print(f"qualidade {r['rel']}: {aviso}")
    if falhas:
        print("falhas:")
        for r in falhas:
            print(f" - {r['rel']}: {r['motivo']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())