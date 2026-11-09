"""Checagens de qualidade da traducao: compara a origem com a saida.

Uso: python scripts/checar_qualidade.py <origem.mdx> <traduzido.mdx>
Codigo de saida 1 se encontrar algum problema.
"""
import io
import re
import sys

INGLES = {"the", "with", "your", "this", "that", "from", "are", "you", "and", "of"}


def ler(caminho):
    with io.open(caminho, encoding="utf-8") as f:
        return f.read()


def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())


def titulos(texto):
    """Devolve (title do frontmatter, primeiro H1); None quando nao existir."""
    fm = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", texto, re.S)
    title = None
    if fm:
        m = re.search(r"^title:\s*(.+?)\s*$", fm.group(1), re.M)
        if m:
            title = m.group(1).strip().strip("\"'")
    corpo = texto[fm.end():] if fm else texto
    corpo = re.sub(r"```.*?```", "", corpo, flags=re.S)
    h1 = re.search(r"^#\s+(.+?)\s*$", corpo, re.M)
    return title, (h1.group(1).strip() if h1 else None)


def prosa_por_linha(texto):
    """Gera (linha, texto) so da prosa: sem codigo, URLs, tags, expressoes e imports."""
    em_cerca = False
    for n, linha in enumerate(texto.splitlines(), 1):
        if linha.strip().startswith("```"):
            em_cerca = not em_cerca
            continue
        if em_cerca or re.match(r"\s*(import|export)\s", linha):
            continue
        l = re.sub(r"`[^`\n]*`", " ", linha)
        l = re.sub(r"\]\([^)\n]*\)", "] ", l)
        l = re.sub(r"https?://\S+", " ", l)
        l = re.sub(r"<[^>\n]*>", " ", l)
        l = re.sub(r"\{[^{}\n]*\}", " ", l)
        l = re.sub(r"^\s*[A-Za-z_]+:\s*", "", l)
        yield n, l


def checar(origem, saida):
    problemas = []

    # a) title e H1 iguais na origem devem sair iguais na traducao
    ot, oh = titulos(origem)
    st, sh = titulos(saida)
    if ot and oh and norm(ot) == norm(oh):
        if st is None or sh is None:
            problemas.append("title ou H1 sumiu na saida")
        elif norm(st) != norm(sh):
            problemas.append(f"title e H1 diferentes na saida: {st!r} x {sh!r}")

    # b) palavras inglesas remanescentes na prosa traduzida
    for n, l in prosa_por_linha(saida):
        for w in re.findall(r"[^\W\d_]+", l.lower()):
            if w in INGLES:
                problemas.append(f"palavra inglesa '{w}' na linha {n} da saida")

    # c) texto alternativo de imagem deve ser traduzido
    alt_o = re.findall(r"!\[([^\]\n]*)\]\(", origem)
    alt_s = re.findall(r"!\[([^\]\n]*)\]\(", saida)
    if len(alt_o) != len(alt_s):
        problemas.append("numero de imagens diferente entre origem e saida")
    for a, b in zip(alt_o, alt_s):
        if a.strip() and norm(a) == norm(b):
            problemas.append(f"texto alternativo nao traduzido: {a!r}")

    # d) atributos e expressoes JSX devem ficar intactos
    achados = set(re.findall(r'\b[\w-]+=(?:"[^"\n]*"|\{[^{}\n]*\})', origem))
    achados |= set(re.findall(r"\{[^{}\n]+\}", origem))
    for trecho in sorted(achados):
        if trecho not in saida:
            problemas.append(f"atributo ou expressao alterado/ausente: {trecho}")

    return problemas


def main():
    if len(sys.argv) != 3:
        print("uso: python scripts/checar_qualidade.py <origem.mdx> <traduzido.mdx>")
        return 2
    problemas = checar(ler(sys.argv[1]), ler(sys.argv[2]))
    if problemas:
        print(f"{len(problemas)} problema(s) de qualidade:")
        for p in problemas:
            print(" -", p)
        return 1
    print("qualidade: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
