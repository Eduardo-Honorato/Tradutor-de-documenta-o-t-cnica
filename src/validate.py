"""Validadores da Etapa 4: conferem se uma traducao quebrou algo.

Dois niveis, ambos SEM chamar LLM:

  1. validate_units(doc, translations)
       Confere cada unidade traduzida ANTES de montar o documento: placeholders,
       quebras de linha, pipes em celulas, chaves/`<` novos (quebram MDX),
       sintaxe de link, aspas do front matter...

  2. validate_document(original, final)
       Confere o documento FINAL contra o original: estrutura de titulos,
       ancoras, inventario de tudo que e protegido (codigo, URLs, tags...),
       formato das tabelas, YAML do front matter e estrutura de blocos.

validate_translation() roda os dois e devolve um ValidationReport com:
  .ok            -> True se nao ha erros (avisos nao reprovam)
  .failed_units  -> indices das unidades a retraduzir
  .final_text    -> documento restaurado (None se restore() falhou)

Erros reprovam; avisos so pedem revisao humana.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.protect import (
    LEFT_GUILLEMET,
    RIGHT_GUILLEMET,
    ProtectedDoc,
    apply_translations,
    detect_line_endings,
    protect,
    restore,
)

try:  # opcionais: se faltarem, a checagem correspondente e pulada
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

try:
    from markdown_it import MarkdownIt as _MarkdownIt
except ImportError:  # pragma: no cover
    _MarkdownIt = None

ERROR = "error"
WARNING = "warning"


# --------------------------------------------------------------------------
# Tipos
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    severity: str                  # "error" | "warning"
    code: str
    message: str
    units: Tuple[int, ...] = ()    # unidades envolvidas (para retraducao)

    def __str__(self) -> str:
        where = f" (unidade {', '.join(map(str, self.units))})" if self.units else ""
        return f"[{self.severity}] {self.code}{where}: {self.message}"


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)
    final_text: Optional[str] = None
    translations: Dict[int, str] = field(default_factory=dict)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def failed_units(self) -> List[int]:
        return sorted({u for i in self.errors for u in i.units})

    def codes(self, severity: Optional[str] = None) -> List[str]:
        return [i.code for i in self.issues if severity is None or i.severity == severity]

    def format(self) -> str:
        if not self.issues:
            return "OK: nenhum problema encontrado"
        return "\n".join(str(i) for i in self.issues)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def _ph_re(prefix: str) -> "re.Pattern[str]":
    return re.compile(re.escape(LEFT_GUILLEMET) + re.escape(prefix) + r"\d+" + re.escape(RIGHT_GUILLEMET))


def normalize_translation(text: str, line_endings: str) -> str:
    """Faz as quebras de linha da traducao seguirem o estilo do documento."""
    if line_endings == "CRLF":
        return re.sub(r"\r?\n", "\r\n", text)
    if line_endings == "LF":
        return text.replace("\r\n", "\n")
    return text


def _strip_placeholders(text: str, ph_re: "re.Pattern[str]") -> str:
    return ph_re.sub("", text)


_LETTER = re.compile(r"[^\W\d_]")
_BLOCK_MARKER = re.compile(r"^[ \t]*(?:#{1,6}[ \t]|>|[-*+][ \t]|\d+[.)][ \t]|```|~~~|:::)", re.M)
_BLANK = re.compile(r"\n[ \t\r]*\n")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _front_matter_zone_end(doc: ProtectedDoc) -> int:
    ph_re = _ph_re(doc.prefix)
    ends = [
        m.end()
        for m in ph_re.finditer(doc.protected_text)
        if doc.placeholder_kinds.get(m.group(0)) == "front_matter"
    ]
    return max(ends) if ends else 0


def _front_matter_unit_indexes(doc: ProtectedDoc) -> Tuple[int, ...]:
    zone = _front_matter_zone_end(doc)
    return tuple(i for i, u in enumerate(doc.translatable_units) if u["end"] <= zone)


def _yaml_value_problem(text: str, quote: Optional[str]) -> Optional[str]:
    """Descreve por que `text` quebraria o YAML se colocado onde o original estava."""
    if "\n" in text or "\r" in text:
        return "quebra de linha dentro do valor"
    if quote == '"':
        if re.search(r'(?<!\\)"', text):
            return 'aspas duplas sem escape dentro de valor entre aspas duplas'
        if text.endswith("\\") and not text.endswith("\\\\"):
            return "barra invertida no fim do valor"
    elif quote == "'":
        if text.replace("''", "").count("'"):
            return "aspa simples sem duplicar dentro de valor entre aspas simples"
    else:
        if ": " in text or " #" in text or text.endswith(":"):
            return "': ' ou ' #' em valor sem aspas"
        if text[:1] in "\"'|>&*!%@`#[{":
            return "valor sem aspas comecando com caractere especial do YAML"
    return None


# --------------------------------------------------------------------------
# 1. Validacao por unidade
# --------------------------------------------------------------------------

def validate_units(doc: ProtectedDoc, translations: Dict[int, str]) -> List[Issue]:
    issues: List[Issue] = []
    units = doc.translatable_units
    ptext = doc.protected_text
    pmap, kinds = doc.placeholder_map, doc.placeholder_kinds
    ph_re = _ph_re(doc.prefix)
    zone_end = _front_matter_zone_end(doc)
    end_to_ph = {m.end(): m.group(0) for m in ph_re.finditer(ptext)}

    for idx in sorted(translations):
        if not 0 <= idx < len(units):
            issues.append(Issue(ERROR, "unknown_unit", f"indice de unidade inexistente: {idx}"))

    for idx, u in enumerate(units):
        if idx not in translations:
            issues.append(Issue(ERROR, "missing_translation", "unidade sem traducao", (idx,)))
            continue
        t = translations[idx]
        orig = u["text"]

        def err(code: str, msg: str) -> None:
            issues.append(Issue(ERROR, code, msg, (idx,)))

        def warn(code: str, msg: str) -> None:
            issues.append(Issue(WARNING, code, msg, (idx,)))

        if not t.strip():
            err("empty_translation", "traducao vazia")
            continue

        # --- placeholders -------------------------------------------------
        orig_phs = Counter(m.group(0) for m in ph_re.finditer(orig))
        new_phs = Counter(m.group(0) for m in ph_re.finditer(t))
        for ph in new_phs:
            if ph not in pmap:
                err("placeholder_unknown", f"placeholder inventado: {ph}")
        missing = orig_phs - new_phs
        if missing:
            err("placeholder_missing", f"placeholder(s) removido(s): {', '.join(sorted(missing))}")
        extra = Counter({k: v for k, v in (new_phs - orig_phs).items() if k in pmap})
        if extra:
            err("placeholder_duplicated", f"placeholder(s) a mais ou duplicado(s): {', '.join(sorted(extra))}")

        # --- estrutura da linha -------------------------------------------
        if "\n" not in orig and ("\n" in t or "\r" in t):
            err("newline_added", "a traducao tem quebra de linha, o original nao")
        if "\n" in orig and _BLANK.search(t):
            err("blank_line_added", "linha em branco criada no meio do paragrafo")
        if u["type"] == "table_cell" and _UNESCAPED_PIPE.search(t):
            err("pipe_in_cell", "'|' sem escape criaria uma coluna nova")
        if len(_BLOCK_MARKER.findall(t)) > len(_BLOCK_MARKER.findall(orig)):
            err("block_marker_added", "a traducao comeca linha com marcador de bloco (#, >, -, 1., ```, :::)")

        # --- caracteres que quebram MDX -----------------------------------
        for ch in "{}<":
            if t.count(ch) > orig.count(ch):
                err("mdx_special_char", f"caractere {ch!r} novo no texto (MDX tentaria interpreta-lo)")

        # --- sintaxe de link ----------------------------------------------
        for ph in orig_phs:
            if kinds.get(ph) == "link_url" and f"]({ph})" in orig and f"]({ph})" not in t:
                err("link_syntax_broken", f"o link ...]({ph}) foi alterado")

        # --- front matter -------------------------------------------------
        if u["end"] <= zone_end and zone_end:
            before = end_to_ph.get(u["start"])
            quote = pmap[before][-1] if before and pmap[before][-1:] in ("'", '"') else None
            problem = _yaml_value_problem(t, quote)
            if problem:
                err("yaml_value_unsafe", f"{u['type']} do front matter: {problem}")

        # --- avisos (nao reprovam) ----------------------------------------
        bare_orig = _strip_placeholders(orig, ph_re)
        if (
            t.strip() == orig.strip()
            and len(_LETTER.findall(bare_orig)) >= 4
            and len(bare_orig.split()) >= 2
        ):
            warn("unchanged", "a traducao e igual ao original")
        if len(orig.strip()) >= 30 and not (len(orig) / 4 <= len(t) <= len(orig) * 4):
            warn("length_ratio", f"tamanho muito diferente ({len(orig)} -> {len(t)} caracteres)")
        for marker in ("**", "__", "~~"):
            if orig.count(marker) != t.count(marker):
                warn("emphasis_changed", f"quantidade de {marker!r} mudou")

    return issues


# --------------------------------------------------------------------------
# 2. Validacao do documento final
# --------------------------------------------------------------------------

def _inventory(doc: ProtectedDoc) -> "Counter[Tuple[str, str]]":
    return Counter((doc.placeholder_kinds[ph], text) for ph, text in doc.placeholder_map.items())


def _split_cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return _UNESCAPED_PIPE.split(s)


def _table_shapes(doc: ProtectedDoc) -> List[Tuple[int, ...]]:
    ph_re = _ph_re(doc.prefix)
    full = re.compile(r"[ \t]*" + ph_re.pattern + r"[ \t]*")
    lines = doc.protected_text.split("\n")
    lines = [ln.rstrip("\r") for ln in lines]

    def is_sep(k: int) -> bool:
        m = full.fullmatch(lines[k])
        return bool(m) and doc.placeholder_kinds.get(m.group(0).strip()) == "table_sep"

    shapes: List[Tuple[int, ...]] = []
    for k in range(len(lines)):
        if not is_sep(k) or k == 0:
            continue
        rows = [len(_split_cells(lines[k - 1]))]
        r = k + 1
        while r < len(lines) and "|" in lines[r] and not is_sep(r):
            rows.append(len(_split_cells(lines[r])))
            r += 1
        shapes.append(tuple(rows))
    return shapes


def _front_matter_block(text: str) -> Optional[str]:
    lines = text.lstrip("\ufeff").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return "\n".join(lines[1:i])
    return None


def _strip_front_matter(text: str) -> str:
    body = text.lstrip("\ufeff")
    lines = body.split("\n")
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() in ("---", "..."):
                return "\n".join(lines[i + 1:])
    return body


def _block_skeleton(text: str) -> List[Tuple[str, int]]:
    md = _MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return [(t.type, t.level) for t in md.parse(_strip_front_matter(text)) if t.type != "inline"]


def _describe(items: "Counter[Tuple[str, str]]", limit: int = 3) -> str:
    out = []
    for (kind, text), n in list(items.items())[:limit]:
        short = text.replace("\n", "\\n")
        out.append(f"{kind}:{short[:50]!r}" + (f" x{n}" if n > 1 else ""))
    more = len(items) - limit
    return ", ".join(out) + (f" (+{more})" if more > 0 else "")


def validate_document(
    original: str,
    final: str,
    original_doc: Optional[ProtectedDoc] = None,
) -> List[Issue]:
    issues: List[Issue] = []
    od = original_doc if original_doc is not None else protect(original)
    fd = protect(final)
    fm_units = _front_matter_unit_indexes(od)

    # --- finais de linha -------------------------------------------------
    eol_o, eol_f = detect_line_endings(original), detect_line_endings(final)
    if eol_o != eol_f:
        issues.append(Issue(ERROR, "line_endings_changed", f"finais de linha mudaram: {eol_o} -> {eol_f}"))

    # --- titulos e ancoras -----------------------------------------------
    levels_o = [h["level"] for h in od.heading_slugs]
    levels_f = [h["level"] for h in fd.heading_slugs]
    if levels_o != levels_f:
        issues.append(Issue(
            ERROR, "headings_changed",
            f"estrutura de titulos mudou: niveis {levels_o[:12]} -> {levels_f[:12]}",
        ))
    else:
        for i, (ho, hf) in enumerate(zip(od.heading_slugs, fd.heading_slugs)):
            if ho["explicit_id"] and ho["explicit_id"] != hf["explicit_id"]:
                issues.append(Issue(
                    ERROR, "anchor_lost",
                    f"titulo {i + 1} perdeu a ancora {{#{ho['explicit_id']}}}",
                ))

    anchors_o = {h["slug"] for h in od.heading_slugs} | {h["explicit_id"] for h in od.heading_slugs if h["explicit_id"]}
    anchors_f = {h["slug"] for h in fd.heading_slugs} | {h["explicit_id"] for h in fd.heading_slugs if h["explicit_id"]}
    for ph, text in od.placeholder_map.items():
        if od.placeholder_kinds[ph] == "link_url" and text.startswith("#"):
            target = text[1:].split()[0] if text[1:].split() else ""
            if target in anchors_o and target not in anchors_f:
                issues.append(Issue(
                    ERROR, "broken_anchor",
                    f"link interno para #{target}, mas esse titulo nao existe mais com esse id",
                ))

    # --- inventario do que e protegido -----------------------------------
    inv_o, inv_f = _inventory(od), _inventory(fd)
    removed = inv_o - inv_f
    added = Counter({k: v for k, v in (inv_f - inv_o).items() if k[0] != "anchor"})
    if removed or added:
        parts = []
        if removed:
            parts.append("sumiu: " + _describe(removed))
        if added:
            parts.append("apareceu: " + _describe(added))
        issues.append(Issue(ERROR, "protected_changed", "; ".join(parts)))

    # --- tabelas ---------------------------------------------------------
    shapes_o, shapes_f = _table_shapes(od), _table_shapes(fd)
    if shapes_o != shapes_f:
        issues.append(Issue(ERROR, "table_shape_changed", f"formato das tabelas mudou: {shapes_o} -> {shapes_f}"))

    # --- front matter (YAML) ---------------------------------------------
    if _yaml is not None:
        block_o, block_f = _front_matter_block(original), _front_matter_block(final)
        if block_o is not None:
            try:
                data_o = _yaml.safe_load(block_o)
            except _yaml.YAMLError:
                data_o = None
            if isinstance(data_o, dict):
                if block_f is None:
                    issues.append(Issue(ERROR, "front_matter_invalid", "o front matter sumiu", fm_units))
                else:
                    try:
                        data_f = _yaml.safe_load(block_f)
                    except _yaml.YAMLError as exc:
                        issues.append(Issue(ERROR, "front_matter_invalid", f"YAML invalido: {str(exc).splitlines()[0]}", fm_units))
                    else:
                        if not isinstance(data_f, dict) or set(data_f) != set(data_o):
                            issues.append(Issue(ERROR, "front_matter_invalid", "as chaves do front matter mudaram", fm_units))
                        else:
                            for key, value in data_o.items():
                                if key in ("title", "description"):
                                    if isinstance(value, str) and not (isinstance(data_f[key], str) and data_f[key].strip()):
                                        issues.append(Issue(ERROR, "front_matter_invalid", f"'{key}' ficou vazio ou deixou de ser texto", fm_units))
                                elif data_f[key] != value:
                                    issues.append(Issue(ERROR, "front_matter_invalid", f"o valor de '{key}' mudou", fm_units))

    # --- estrutura de blocos (markdown-it-py) ----------------------------
    if _MarkdownIt is not None:
        sk_o, sk_f = _block_skeleton(original), _block_skeleton(final)
        if sk_o != sk_f:
            first = next((i for i, (a, b) in enumerate(zip(sk_o, sk_f)) if a != b), min(len(sk_o), len(sk_f)))
            a = sk_o[first][0] if first < len(sk_o) else "(fim)"
            b = sk_f[first][0] if first < len(sk_f) else "(fim)"
            issues.append(Issue(
                ERROR, "block_structure_changed",
                f"a estrutura de blocos mudou no bloco {first + 1}: {a} -> {b} ({len(sk_o)} -> {len(sk_f)} blocos)",
            ))

    return issues


# --------------------------------------------------------------------------
# Atalho: tudo junto
# --------------------------------------------------------------------------

def validate_translation(
    original: str,
    translations: Dict[int, str],
    doc: Optional[ProtectedDoc] = None,
) -> ValidationReport:
    """Valida uma traducao completa (indice da unidade -> texto traduzido)."""
    doc = doc if doc is not None else protect(original)
    tr = {i: normalize_translation(t, doc.line_endings) for i, t in translations.items()}
    report = ValidationReport(translations=tr)
    report.issues.extend(validate_units(doc, tr))

    applicable = {i: t for i, t in tr.items() if 0 <= i < len(doc.translatable_units)}
    try:
        report.final_text = restore(apply_translations(doc, applicable))
    except ValueError as exc:
        report.issues.append(Issue(ERROR, "restore_failed", str(exc)))
        return report

    report.issues.extend(validate_document(original, report.final_text, doc))
    return report
