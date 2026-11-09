"""Protecao de documentos MDX para traducao (Etapa 3).

Ideia central
-------------
O texto original NUNCA e reescrito. Primeiro descobrimos *quais trechos* sao
intocaveis (codigo, URLs, tags JSX, imports, ...), cada trecho e "reivindicado"
uma unica vez sobre o texto original e nenhum trecho pode se sobrepor a outro.
Depois cada trecho vira um placeholder e guardamos o original num mapa.

    protect(texto)  -> ProtectedDoc      (texto com placeholders + mapa + unidades)
    restore(doc)    -> str               (volta ao original, byte a byte)

Garantias (testadas):
  * restore(protect(x)) == x para qualquer texto, inclusive CRLF e vazio;
  * nenhum trecho protegido aparece em unidades traduziveis;
  * placeholders nunca colidem com texto que ja existe no documento;
  * restore() falha com erro explicito se sobrar/inventarem um placeholder.

Unidades traduziveis
--------------------
`translatable_units` lista exatamente quais pedacos de `protected_text` podem
ser enviados ao tradutor (com offsets `start`/`end`). Tudo fora das unidades
(marcadores de lista, `#`, `|`, `>`, quebras de linha) nunca passa pelo LLM.
`apply_translations()` devolve um novo ProtectedDoc com as unidades trocadas.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

LEFT_GUILLEMET = "\u27e6"   # ⟦
RIGHT_GUILLEMET = "\u27e7"  # ⟧

_MASK = "\x00"


# --------------------------------------------------------------------------
# Tipos publicos
# --------------------------------------------------------------------------

@dataclass
class Span:
    """Trecho [start, end) do texto original que sera protegido."""
    start: int
    end: int
    kind: str


@dataclass
class ProtectedDoc:
    """Resultado de protect()."""
    protected_text: str
    placeholder_map: Dict[str, str]
    line_endings: str                      # "LF", "CRLF" ou "misto"
    translatable_units: List[dict]         # {type, text, start, end, position}
    placeholder_kinds: Dict[str, str] = field(default_factory=dict)
    prefix: str = "P"                      # placeholders sao ⟦{prefix}{n}⟧
    heading_slugs: List[dict] = field(default_factory=list)  # so metadado


# --------------------------------------------------------------------------
# Utilidades basicas
# --------------------------------------------------------------------------

def detect_line_endings(text: str) -> str:
    """'LF', 'CRLF' ou 'misto'. Texto sem quebra de linha conta como 'LF'."""
    total = text.count("\n")
    if total == 0:
        return "LF"
    crlf = text.count("\r\n")
    if crlf == 0:
        return "LF"
    if crlf == total:
        return "CRLF"
    return "misto"


def _lines(text: str) -> List[Tuple[int, int]]:
    """[(inicio, fim_do_conteudo)] por linha; o fim exclui '\\r' e '\\n'."""
    out: List[Tuple[int, int]] = []
    pos = 0
    n = len(text)
    while True:
        nl = text.find("\n", pos)
        end = n if nl == -1 else nl
        content_end = end - 1 if end > pos and text[end - 1] == "\r" else end
        out.append((pos, content_end))
        if nl == -1:
            break
        pos = nl + 1
    return out


def _placeholder_re(prefix: str) -> "re.Pattern[str]":
    return re.compile(
        re.escape(LEFT_GUILLEMET) + re.escape(prefix) + r"(\d+)" + re.escape(RIGHT_GUILLEMET)
    )


def _choose_prefix(text: str) -> str:
    """Escolhe um prefixo cujo padrao de placeholder NAO existe no texto."""
    for i in range(1000):
        prefix = "P" + "_" * i
        if not _placeholder_re(prefix).search(text):
            return prefix
    raise ValueError("nao foi possivel escolher um formato de placeholder sem colisao")


_BLANK_LINE = re.compile(r"\n[ \t\r]*\n")


class _Claims:
    """Registro de trechos reivindicados; garante que nada se sobreponha."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.covered = bytearray(len(text))
        self.spans: List[Span] = []

    def free(self, start: int, end: int) -> bool:
        return 0 <= start < end <= len(self.text) and self.covered.find(1, start, end) == -1

    def claim(self, start: int, end: int, kind: str) -> bool:
        if not self.free(start, end):
            return False
        self.covered[start:end] = b"\x01" * (end - start)
        self.spans.append(Span(start, end, kind))
        return True

    def masked(self) -> str:
        """Texto com os trechos ja reivindicados trocados por \\x00 (mesmos offsets)."""
        if not self.spans:
            return self.text
        parts: List[str] = []
        pos = 0
        for sp in sorted(self.spans, key=lambda s: s.start):
            parts.append(self.text[pos:sp.start])
            parts.append(_MASK * (sp.end - sp.start))
            pos = sp.end
        parts.append(self.text[pos:])
        return "".join(parts)


# --------------------------------------------------------------------------
# Scanners pequenos (strings, chaves, parenteses, tags)
# --------------------------------------------------------------------------

def _skip_string(t: str, i: int) -> Optional[int]:
    """t[i] abre uma string ('"`); devolve o indice apos o fecho, ou None."""
    q = t[i]
    n = len(t)
    j = i + 1
    while j < n:
        c = t[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        if c == "\n" and q != "`":
            return None
        j += 1
    return None


def _scan_balanced_impl(t: str, i: int, quotes: bool) -> Optional[int]:
    depth = 0
    n = len(t)
    j = i
    while j < n:
        c = t[j]
        if quotes and c in "\"'`":
            k = _skip_string(t, j)
            if k is None:
                return None
            j = k
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def _scan_balanced(t: str, i: int) -> Optional[int]:
    """t[i] == '{'; devolve o indice apos a '}' correspondente, ou None."""
    end = _scan_balanced_impl(t, i, True)
    if end is None:
        end = _scan_balanced_impl(t, i, False)
    return end


_TAG_NAME = re.compile(r"[A-Za-z][\w.:-]*")
_ATTR_NAME = re.compile(r"[A-Za-z_$][\w$:.-]*")
_WS = " \t\r\n"


def _scan_tag(t: str, i: int) -> Optional[int]:
    """t[i] == '<'; devolve o fim da tag JSX/HTML (aberta, fechada ou
    auto-fechada), ou None se nao for uma tag."""
    n = len(t)
    j = i + 1
    if j >= n:
        return None
    if t[j] == "/":
        j += 1
    if j < n and t[j] == ">":          # fragmento <> ou </>
        return j + 1
    m = _TAG_NAME.match(t, j)
    if not m:
        return None
    j = m.end()
    if j >= n or t[j] not in " \t\r\n/>":
        return None
    while True:
        while j < n and t[j] in _WS:
            j += 1
        if j >= n:
            return None
        ch = t[j]
        if ch == ">":
            return j + 1
        if ch == "/":
            if j + 1 < n and t[j + 1] == ">":
                return j + 2
            return None
        if ch == "{":                    # {...props}
            k = _scan_balanced(t, j)
            if k is None:
                return None
            j = k
            continue
        m2 = _ATTR_NAME.match(t, j)
        if not m2:
            return None
        j = m2.end()
        k = j
        while k < n and t[k] in _WS:
            k += 1
        if k < n and t[k] == "=":
            k += 1
            while k < n and t[k] in _WS:
                k += 1
            if k >= n:
                return None
            q = t[k]
            if q in "\"'":
                e = t.find(q, k + 1)
                if e == -1:
                    return None
                j = e + 1
            elif q == "{":
                e = _scan_balanced(t, k)
                if e is None:
                    return None
                j = e
            else:
                return None


def _scan_paren(t: str, start: int) -> Optional[int]:
    """Procura o ')' que fecha um '(' aberto logo antes de `start`."""
    depth = 1
    n = len(t)
    i = start
    while i < n:
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n" and _BLANK_LINE.match(t, i):
            return None
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _scan_statement(t: str, start: int) -> Optional[int]:
    """Fim de uma instrucao JS (export ...) que pode ocupar varias linhas."""
    n = len(t)
    i = start
    depth = 0
    cont = ("=", "=>", ",", "+", "&&", "||", "?")
    while i < n:
        c = t[i]
        if c in "\"'`":
            k = _skip_string(t, i)
            if k is None:
                return None
            i = k
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
            if depth < 0:
                return None
        elif c == "\n" and depth == 0:
            seg = t[t.rfind("\n", 0, i) + 1:i].rstrip()
            if seg.endswith(cont):
                i += 1
                continue
            end = i - 1 if t[i - 1] == "\r" else i
            return end if end > start else None
        i += 1
    if depth != 0:
        return None
    end = n - 1 if n > start and t[n - 1] == "\r" else n
    return end if end > start else None


# --------------------------------------------------------------------------
# Detectores (cada um recebe o texto "mascarado" e reivindica trechos)
# --------------------------------------------------------------------------

_FM_KEY = re.compile(r"^(title|description)[ \t]*:[ \t]*(.*?)[ \t]*$")


def _detect_front_matter(text: str, claims: _Claims) -> List[Tuple[int, int, str]]:
    """Protege o front matter MENOS os valores de title/description.

    O YAML nao e reserializado: so os valores entre aspas (ou sem aspas) de
    `title:` e `description:` ficam visiveis; o resto vira placeholders.
    Devolve [(inicio, fim, 'title'|'description')] em coordenadas originais.
    """
    lines = _lines(text)
    bom = 1 if text.startswith("\ufeff") else 0
    s0, e0 = lines[0]
    if text[s0 + bom:e0].rstrip() != "---":
        return []
    close = None
    for idx in range(1, len(lines)):
        s, e = lines[idx]
        if text[s:e].rstrip() in ("---", "..."):
            close = idx
            break
    if close is None:
        return []

    fm_start = s0 + bom
    fm_end = lines[close][1]
    regions: List[Tuple[int, int, str]] = []
    for idx in range(1, close):
        s, e = lines[idx]
        m = _FM_KEY.match(text[s:e])
        if not m or m.group(2) == "":
            continue
        rest = m.group(2)
        if len(rest) >= 2 and rest[0] in "\"'" and rest[-1] == rest[0]:
            vs, ve = s + m.start(2) + 1, s + m.end(2) - 1
        elif rest[0] in "\"'|>&*!%@`#[{":
            continue
        else:
            vs, ve = s + m.start(2), s + m.end(2)
        if vs < ve:
            regions.append((vs, ve, m.group(1)))

    pos = fm_start
    for vs, ve, _key in regions:
        if vs > pos:
            claims.claim(pos, vs, "front_matter")
        pos = ve
    if fm_end > pos:
        claims.claim(pos, fm_end, "front_matter")
    return regions


_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")


def _detect_fenced(t: str, claims: _Claims) -> None:
    """Blocos ``` / ~~~ (com meta, 4+ crases, CRLF, sem fechamento)."""
    lines = _lines(t)
    open_: Optional[Tuple[int, str, int]] = None
    for s, e in lines:
        content = t[s:e]
        if open_ is None:
            m = _FENCE_OPEN.match(content)
            if m:
                fence, info = m.group(1), m.group(2)
                if fence[0] == "`" and "`" in info:
                    continue                      # ```code``` numa linha so
                open_ = (s, fence[0], len(fence))
        else:
            m = _FENCE_CLOSE.match(content)
            if m and m.group(1)[0] == open_[1] and len(m.group(1)) >= open_[2]:
                claims.claim(open_[0], e, "fenced_code")
                open_ = None
    if open_ is not None:                          # sem fechamento: vai ate o fim
        last_s, last_e = lines[-1]
        end = last_e if last_e > last_s else (lines[-2][1] if len(lines) > 1 else last_e)
        claims.claim(open_[0], end, "fenced_code")


_LIST_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])(?:\s|$)")


def _indent_width(c: str) -> int:
    w = 0
    for ch in c:
        if ch == " ":
            w += 1
        elif ch == "\t":
            w += 4 - (w % 4)
        else:
            break
    return w


def _detect_indented(t: str, claims: _Claims) -> None:
    """Codigo indentado (4+ espacos apos linha em branco).

    Ignora continuacao de item de lista e filhos de tags (<...) ou {...}:
    nesses casos o texto e prosa, nao codigo.
    """
    lines = _lines(t)
    n = len(lines)
    prev_blank = True
    last_nonblank: Optional[str] = None
    skip = False
    i = 0
    while i < n:
        s, e = lines[i]
        c = t[s:e]
        if c.strip() == "":
            prev_blank = True
            i += 1
            continue
        if _indent_width(c) < 4:
            skip = False
            last_nonblank = c
            prev_blank = False
            i += 1
            continue
        if prev_blank and not skip:
            ctx = last_nonblank
            if ctx is not None and (_LIST_LINE.match(ctx) or ctx.lstrip().startswith(("<", "{"))):
                skip = True
            else:
                last_code = i
                k = i
                while k < n:
                    ck = t[lines[k][0]:lines[k][1]]
                    if ck.strip() == "":
                        k += 1
                        continue
                    if _indent_width(ck) >= 4:
                        last_code = k
                        k += 1
                        continue
                    break
                claims.claim(lines[i][0], lines[last_code][1], "indented_code")
                last_nonblank = t[lines[last_code][0]:lines[last_code][1]]
                prev_blank = False
                i = last_code + 1
                continue
        last_nonblank = c
        prev_blank = False
        i += 1


_IMPORT = re.compile(
    r"""^import[ \t]+(?:[\w$*\s,{}]+?[ \t\r\n]+from[ \t\r\n]+)?(['"])[^'"\r\n]+\1[ \t]*;?""",
    re.M,
)
_EXPORT = re.compile(
    r"^export[ \t]+(?:default\b|const\b|let\b|var\b|function\b|async\b|class\b|type\b|interface\b|\{|\*)",
    re.M,
)


def _detect_import_export(t: str, claims: _Claims) -> None:
    for m in _IMPORT.finditer(t):
        claims.claim(m.start(), m.end(), "import")
    for m in _EXPORT.finditer(t):
        end = _scan_statement(t, m.start())
        if end is not None:
            claims.claim(m.start(), end, "export")


def _detect_comments(t: str, claims: _Claims) -> None:
    for open_tok, close_tok in (("<!--", "-->"), ("{/*", "*/}")):
        i = 0
        while True:
            s = t.find(open_tok, i)
            if s == -1:
                break
            e = t.find(close_tok, s + len(open_tok))
            if e == -1:
                break
            e += len(close_tok)
            claims.claim(s, e, "comment")
            i = e


_BACKTICK_RUN = re.compile(r"`+")


def _detect_inline_code(t: str, claims: _Claims) -> None:
    """Codigo inline a la CommonMark: abre com N crases, fecha com N crases."""
    runs = [(m.start(), m.end()) for m in _BACKTICK_RUN.finditer(t)]
    by_size: Dict[int, List[int]] = {}
    for idx, (rs, re_) in enumerate(runs):
        by_size.setdefault(re_ - rs, []).append(idx)
    i = 0
    while i < len(runs):
        s, e = runs[i]
        bs = 0
        k = s - 1
        while k >= 0 and t[k] == "\\":
            bs += 1
            k -= 1
        if bs % 2 == 1:                 # crase escapada: a primeira e literal
            s += 1
            if s >= e:
                i += 1
                continue
        size = e - s
        same = by_size.get(size, [])
        pos = bisect.bisect_right(same, i)
        if pos >= len(same):
            i += 1
            continue
        found = same[pos]
        s2, e2 = runs[found]
        if _BLANK_LINE.search(t, e, s2):
            i += 1
            continue
        if claims.claim(s, e2, "inline_code"):
            i = found + 1
        else:
            i += 1


_AUTOLINK = re.compile(
    r"<(?:[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>\x00]*|[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+)>"
)


def _detect_autolinks(t: str, claims: _Claims) -> None:
    for m in _AUTOLINK.finditer(t):
        claims.claim(m.start(), m.end(), "autolink")


def _detect_tags(t: str, claims: _Claims) -> None:
    """Cada tag (abertura, fechamento, auto-fechada) e protegida SEPARADAMENTE;
    o texto entre as tags continua traduzivel."""
    pos = 0
    while True:
        i = t.find("<", pos)
        if i == -1:
            break
        end = _scan_tag(t, i)
        if end is not None and claims.claim(i, end, "tag"):
            pos = end
        else:
            pos = i + 1


_ANCHOR = re.compile(r"\{#[^\s{}]+\}")


def _detect_expressions(t: str, claims: _Claims) -> None:
    """{expressoes} MDX e ancoras de titulo {#id}."""
    pos = 0
    while True:
        i = t.find("{", pos)
        if i == -1:
            break
        end = _scan_balanced(t, i)
        if end is None or _BLANK_LINE.search(t, i, end):
            pos = i + 1
            continue
        kind = "anchor" if _ANCHOR.fullmatch(t, i, end) else "expression"
        if claims.claim(i, end, kind):
            pos = end
        else:
            pos = i + 1


_ADMONITION = re.compile(r"^[ \t]*:{3,}[ \t]*(?:[A-Za-z][\w-]*)?", re.M)
_TABLE_SEP = re.compile(
    r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*(?=\r?$)", re.M
)


def _detect_block_syntax(t: str, claims: _Claims) -> None:
    for m in _ADMONITION.finditer(t):
        claims.claim(m.start(), m.end(), "admonition_marker")
    for m in _TABLE_SEP.finditer(t):
        if "|" in m.group(0):
            claims.claim(m.start(), m.end(), "table_sep")


_REF_DEF = re.compile(r"^[ ]{0,3}\[(?!\^)([^\]\r\n]+)\]:[ \t]*\S[^\r\n]*", re.M)
_FOOTNOTE_DEF = re.compile(r"^[ ]{0,3}\[\^[^\]\s]+\]:", re.M)
_FOOTNOTE_REF = re.compile(r"\[\^[^\]\s]+\]")
_REF_USE = re.compile(r"\]\[([^\]\r\n]*)\]")
_BRACKETED = re.compile(r"\[([^\[\]\r\n]+)\](?!\(|:)")


def _norm_label(s: str) -> str:
    return " ".join(s.split()).lower()


def _detect_references(t: str, claims: _Claims) -> None:
    """Links por referencia: definicoes e rotulos nunca podem mudar."""
    labels = set()
    for m in _REF_DEF.finditer(t):
        if claims.claim(m.start(), m.end(), "ref_definition"):
            labels.add(_norm_label(m.group(1)))
    for m in _FOOTNOTE_DEF.finditer(t):
        claims.claim(m.start(), m.end(), "footnote_def")
    for m in _REF_USE.finditer(t):
        claims.claim(m.start() + 1, m.end(), "ref_label")
    if labels:
        for m in _BRACKETED.finditer(t):
            if _norm_label(m.group(1)) in labels:
                claims.claim(m.start(), m.end(), "ref_label")
    for m in _FOOTNOTE_REF.finditer(t):
        claims.claim(m.start(), m.end(), "footnote_ref")


def _detect_link_destinations(t: str, claims: _Claims) -> None:
    """[texto](URL) e ![alt](URL): protege so o que esta entre os parenteses."""
    i = 0
    while True:
        j = t.find("](", i)
        if j == -1:
            break
        start = j + 2
        end = _scan_paren(t, start)
        if end is not None and end > start:
            claims.claim(start, end, "link_url")
        i = j + 2


_BARE_URL = re.compile(r"https?://[^\s<>\x00]+")


def _detect_bare_urls(t: str, claims: _Claims) -> None:
    for m in _BARE_URL.finditer(t):
        s, e = m.start(), m.end()
        while e > s and t[e - 1] in ".,;:!?'\"*_":
            e -= 1
        while e > s and t[e - 1] == ")" and t[s:e].count(")") > t[s:e].count("("):
            e -= 1
        while e > s and t[e - 1] == "]" and t[s:e].count("]") > t[s:e].count("["):
            e -= 1
        if e - s > len("https://"):
            claims.claim(s, e, "url")


class _Collector:
    """Coleta candidatos sem decidir conflitos (a decisao e feita depois)."""

    def __init__(self) -> None:
        self.spans: List[Span] = []

    def claim(self, start: int, end: int, kind: str) -> bool:
        self.spans.append(Span(start, end, kind))
        return True


def _detect_inline_constructs(t: str, claims: _Claims) -> None:
    """Codigo inline, autolinks, tags JSX e {expressoes} disputam o mesmo texto.

    Quem COMECA primeiro ganha (empate: o mais longo). Assim `<A x={`y`} />`
    continua uma tag so, e `` `<Tabs>` `` continua codigo inline.
    """
    col = _Collector()
    for detector in (_detect_inline_code, _detect_autolinks, _detect_tags, _detect_expressions):
        detector(t, col)  # type: ignore[arg-type]
    for sp in sorted(col.spans, key=lambda s: (s.start, -(s.end - s.start))):
        claims.claim(sp.start, sp.end, sp.kind)


# Ordem = prioridade entre grupos. Cada detector so ve o texto ainda nao reivindicado.
_DETECTORS = (
    _detect_fenced,
    _detect_indented,
    _detect_import_export,
    _detect_comments,
    _detect_inline_constructs,
    _detect_block_syntax,
    _detect_references,
    _detect_link_destinations,
    _detect_bare_urls,
)


# --------------------------------------------------------------------------
# Montagem do texto protegido
# --------------------------------------------------------------------------

def _apply(
    text: str,
    spans: List[Span],
    prefix: str,
    regions: List[Tuple[int, int, str]],
):
    spans = sorted(spans, key=lambda s: s.start)
    out: List[str] = []
    out_len = 0
    pos = 0
    pmap: Dict[str, str] = {}
    kinds: Dict[str, str] = {}
    fm_zone_end = 0
    region_pos: List[Tuple[int, int, str]] = []
    segs: List[Tuple[int, int, int, int, bool]] = []

    def emit_gap(a: int, b: int) -> None:
        nonlocal out_len
        if b <= a:
            return
        segs.append((a, b, out_len, out_len + (b - a), False))
        for rs, re_, key in regions:
            if a <= rs and re_ <= b:
                region_pos.append((out_len + rs - a, out_len + re_ - a, key))
        seg = text[a:b]
        out.append(seg)
        out_len += len(seg)

    n = 0
    for sp in spans:
        if sp.start < pos:
            raise AssertionError("trechos protegidos se sobrepoem (bug interno)")
        emit_gap(pos, sp.start)
        n += 1
        ph = f"{LEFT_GUILLEMET}{prefix}{n}{RIGHT_GUILLEMET}"
        pmap[ph] = text[sp.start:sp.end]
        kinds[ph] = sp.kind
        segs.append((sp.start, sp.end, out_len, out_len + len(ph), True))
        out.append(ph)
        out_len += len(ph)
        if sp.kind == "front_matter":
            fm_zone_end = out_len
        pos = sp.end
    emit_gap(pos, len(text))
    if len(region_pos) != len(regions):
        # Valor do front matter com trecho protegido no meio (ex.: codigo inline
        # no title): leva as bordas da regiao do texto original para o protegido.
        region_pos = []
        for rs, re_, key in regions:
            o_start = o_end = None
            for oa, ob, pa, pb, is_span in segs:
                if o_start is None and oa <= rs < ob:
                    if is_span and rs != oa:
                        break
                    o_start = pa if is_span else pa + (rs - oa)
                if o_end is None and oa < re_ <= ob:
                    if is_span and re_ != ob:
                        break
                    o_end = pb if is_span else pa + (re_ - oa)
            if o_start is None or o_end is None or o_end < o_start:
                raise AssertionError("valor do front matter nao encontrado (bug interno)")
            region_pos.append((o_start, o_end, key))
    return "".join(out), pmap, kinds, fm_zone_end, sorted(region_pos)


# --------------------------------------------------------------------------
# Unidades traduziveis e metadados de titulos
# --------------------------------------------------------------------------

_HAS_LETTER = re.compile(r"[^\W\d_]")
_HEADING = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?(.*?)[ \t]*$")
_QUOTE = re.compile(r"^[ \t]*(?:>[ \t]?)+(.*?)[ \t]*$")
_EXPLICIT_ID = re.compile(r"\{#([^\s{}]+)\}\s*$")


def _slugify(text: str) -> str:
    """Slug aproximado (estilo GitHub). Metadado: so para conferir ancoras depois."""
    text = _EXPLICIT_ID.sub("", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"[`*_~\[\]]", "", text).strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s]+", "-", text).strip("-")


def _extract_units(
    ptext: str,
    pmap: Dict[str, str],
    kinds: Dict[str, str],
    prefix: str,
    fm_zone_end: int,
    region_pos: List[Tuple[int, int, str]],
):
    ph_re = _placeholder_re(prefix)
    full_ph = re.compile(r"[ \t]*" + ph_re.pattern + r"[ \t]*")

    def has_letter(s: str) -> bool:
        return bool(_HAS_LETTER.search(ph_re.sub("", s)))

    units: List[dict] = []
    headings: List[dict] = []

    for ps, pe, key in region_pos:
        units.append({"type": key, "text": ptext[ps:pe], "start": ps, "end": pe})

    lines = _lines(ptext)

    def sep_line(idx: int) -> bool:
        s, e = lines[idx]
        m = full_ph.fullmatch(ptext[s:e])
        return bool(m) and kinds.get(m.group(0).strip()) == "table_sep"

    table_rows = set()
    for k in range(len(lines)):
        if sep_line(k):
            if k > 0 and lines[k - 1][1] > lines[k - 1][0]:
                table_rows.add(k - 1)
            r = k + 1
            while r < len(lines) and "|" in ptext[lines[r][0]:lines[r][1]] and not sep_line(r):
                table_rows.add(r)
                r += 1

    para: Optional[List[int]] = None

    def flush() -> None:
        nonlocal para
        if para is not None:
            text = ptext[para[0]:para[1]]
            if has_letter(text):
                units.append({"type": "prose", "text": text, "start": para[0], "end": para[1]})
            para = None

    for idx, (s, e) in enumerate(lines):
        if fm_zone_end and e <= fm_zone_end and s < fm_zone_end:
            continue
        content = ptext[s:e]
        if not has_letter(content):
            flush()
            continue
        if full_ph.fullmatch(content):
            flush()
            continue

        m = _HEADING.match(content)
        if m:
            flush()
            if has_letter(m.group(2)):
                ts, te = s + m.start(2), s + m.end(2)
                units.append({"type": "title", "text": ptext[ts:te], "start": ts, "end": te})
                original = ph_re.sub(lambda mm: pmap.get(mm.group(0), mm.group(0)), m.group(2))
                ident = _EXPLICIT_ID.search(original)
                headings.append({
                    "level": len(m.group(1)),
                    "text": _EXPLICIT_ID.sub("", original).strip(),
                    "slug": _slugify(original),
                    "explicit_id": ident.group(1) if ident else None,
                })
            continue

        if idx in table_rows:
            flush()
            cs = s
            k = s
            while k <= e:
                if k == e or (ptext[k] == "|" and (k == s or ptext[k - 1] != "\\")):
                    a, b = cs, k
                    while a < b and ptext[a] in " \t":
                        a += 1
                    while b > a and ptext[b - 1] in " \t":
                        b -= 1
                    if b > a and has_letter(ptext[a:b]):
                        units.append({"type": "table_cell", "text": ptext[a:b], "start": a, "end": b})
                    cs = k + 1
                k += 1
            continue

        m = _LIST_ITEM.match(content)
        if m:
            flush()
            if m.group(1) and has_letter(m.group(1)):
                ts, te = s + m.start(1), s + m.end(1)
                units.append({"type": "prose", "text": ptext[ts:te], "start": ts, "end": te})
            continue

        m = _QUOTE.match(content)
        if m:
            flush()
            if m.group(1) and has_letter(m.group(1)):
                ts, te = s + m.start(1), s + m.end(1)
                units.append({"type": "prose", "text": ptext[ts:te], "start": ts, "end": te})
            continue

        ls = s + (len(content) - len(content.lstrip()))
        le = s + len(content.rstrip())
        if para is None:
            para = [ls, le]
        else:
            para[1] = le

    flush()
    units.sort(key=lambda u: u["start"])
    for i, u in enumerate(units):
        u["position"] = i
    return units, headings


# --------------------------------------------------------------------------
# API publica
# --------------------------------------------------------------------------

def protect(text: str) -> ProtectedDoc:
    """Troca os trechos intocaveis por placeholders e lista o que e traduzivel."""
    claims = _Claims(text)
    regions = _detect_front_matter(text, claims)
    for detector in _DETECTORS:
        detector(claims.masked(), claims)

    prefix = _choose_prefix(text)
    ptext, pmap, kinds, fm_zone_end, region_pos = _apply(text, claims.spans, prefix, regions)
    units, headings = _extract_units(ptext, pmap, kinds, prefix, fm_zone_end, region_pos)
    return ProtectedDoc(
        protected_text=ptext,
        placeholder_map=pmap,
        line_endings=detect_line_endings(text),
        translatable_units=units,
        placeholder_kinds=kinds,
        prefix=prefix,
        heading_slugs=headings,
    )


def restore(protected_doc: ProtectedDoc) -> str:
    """Devolve cada placeholder ao seu conteudo original (uma unica passada).

    Levanta ValueError se existir um placeholder desconhecido (ex.: inventado
    pelo tradutor) ou se algum placeholder sobrar no resultado.
    """
    ph_re = _placeholder_re(protected_doc.prefix)
    pmap = protected_doc.placeholder_map
    unknown: List[str] = []

    def sub(m: "re.Match[str]") -> str:
        key = m.group(0)
        if key not in pmap:
            unknown.append(key)
            return key
        return pmap[key]

    out = ph_re.sub(sub, protected_doc.protected_text)
    if unknown:
        raise ValueError(f"restore() falhou: placeholder(s) desconhecido(s): {unknown}")
    leftover = ph_re.findall(out)
    if leftover:
        raise ValueError(f"restore() falhou: {len(leftover)} placeholder(s) sobraram no texto")
    return out


def apply_translations(doc: ProtectedDoc, translations: Dict[int, str]) -> ProtectedDoc:
    """Substitui unidades pelo texto traduzido (indice da unidade -> novo texto).

    Devolve um novo ProtectedDoc (as unidades antigas deixam de valer, entao a
    lista vem vazia). Use restore() no resultado para obter o documento final.
    """
    text = doc.protected_text
    for idx in sorted(translations, reverse=True):
        u = doc.translatable_units[idx]
        text = text[:u["start"]] + translations[idx] + text[u["end"]:]
    return replace(doc, protected_text=text, translatable_units=[])
