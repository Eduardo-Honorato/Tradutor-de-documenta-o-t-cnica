"""Funções de tradução integradas ao fluxo Etapa 5.

Fluxo: protect() → traduz units → apply_translations() → restore() → validate_translation().

Novidades em relação à Etapa 4:
- Usa translate_client.py para chamar o proxy NVIDIA via .env
- Validação pós-tradução com retry e fallback seguro
- Cache local de traduções por hash
- Nenhum texto parcial retornado em falha
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.protect import protect, apply_translations, restore
from src.validate import validate_translation
from src.translate_client import translate_with_retry

# ---------------------------------------------------------------------------
# Caminhos e configuração
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).parent
_OUT_DIR = _BASE_DIR / "out"
_CACHE_DIR = _BASE_DIR / ".translate_cache"
_GITIGNORE = _BASE_DIR / ".gitignore"

# Garante pastas
if not _OUT_DIR.exists():
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
if not _CACHE_DIR.exists():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Cache simples por hash da unidade (arquivo local, .gitignore já cobre .translate_cache/)
# ---------------------------------------------------------------------------

_translation_cache: Dict[str, str] = {}


def _unit_hash(unit: dict) -> str:
    """Gera hash a partir do texto da unidade + type para cache consistente."""
    key_parts = f"{unit['type']}:{unit['text']}"
    return hashlib.sha256(key_parts.encode("utf-8")).hexdigest()


def _load_cache() -> None:
    """Carrega cache do disco se existir."""
    cache_file = _CACHE_DIR / "cache.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                global _translation_cache
                _translation_cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _translation_cache = {}


def _save_cache() -> None:
    """Salva cache no disco."""
    cache_file = _CACHE_DIR / "cache.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(_translation_cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _in_cache(unit_hash: str) -> Optional[str]:
    """Retorna texto traduzido do cache se existir."""
    return _translation_cache.get(unit_hash)


def _store_in_cache(unit_hash: str, translated: str) -> None:
    """Armazena texto traduzido no cache."""
    _translation_cache[unit_hash] = translated
    _save_cache()


# ---------------------------------------------------------------------------
# Função principal: translate_file
# ---------------------------------------------------------------------------

def translate_file(
    original_text: str,
    max_retries: int = 2,
    use_cache: bool = True,
) -> Tuple[Optional[str], object, bool]:
    """Executa o fluxo completo de tradução Etapa 5.

    Etapas:
    A. protect() — gera ProtectedDoc com placeholders
    B. Extrai units traduzíveis
    C. Traduz cada unit (com retry + cache)
    D. apply_translations() + restore() — monta documento final
    E. validate_translation() — confirma integridade

    Retorna:
        (final_text, report, success)
        - final_text: documento restaurado (None se falhou completamente)
        - report: ValidationReport com issues
        - success: True se validate_translation() ok (avisos não contam como falha)
    """

    # Carregar cache
    _load_cache()

    # --- Fase A: proteção ---
    doc = protect(original_text)

    # --- Fase B: tradução de units ---
    translated_units: Dict[int, str] = {}
    cache_hits = 0
    cache_misses = 0

    for idx, unit in enumerate(doc.translatable_units):
        unit_hash = _unit_hash(unit)

        # Verificar cache
        if use_cache and unit_hash in _translation_cache:
            translated_units[idx] = _translation_cache[unit_hash]
            cache_hits += 1
            continue

        cache_misses += 1

        # Traduzir unit
        original_unit_text = unit["text"]
        translated = translate_with_retry(
            original_unit_text,
            max_retries=max_retries,
        )

        # Validar tipo de retorno: deve ser str ou None (type annotation do cliente)
        if translated is not None and not isinstance(translated, str):
            # Mock ou cliente bugado retornou dict/outro tipo; tratar como erro explícito
            report = validate_translation(original_text, {}, doc)
            report.issues.append(type(
                "Issue", (), {"severity": "error", "code": "translate_type_error",
                              "message": f"translate_with_retry retornou {type(translated).__name__} em vez de str", "units": (idx,)})()
            )
            return (None, report, False)

        if translated is None:
            # Em caso de falha total do cliente: manter original
            translated = original_unit_text
            # Ainda assim continuar — validação apontará o problema

        translated_units[idx] = translated

        # Armazena no cache
        if use_cache:
            _store_in_cache(unit_hash, translated)

    # --- Fase C: restauração e validação ---
    # Aplicar translations
    doc_after_apply = apply_translations(doc, translated_units)

    # Restaurar
    try:
        final_text = restore(doc_after_apply)
    except ValueError as exc:
        # restore falhou (placeholder inventado/sobrou)
        report = validate_translation(original_text, {}, doc)
        report.issues.append(type("Issue", (), {"severity": "error", "code": "restore_failed",
                                                   "message": str(exc), "units": ()})())
        return (None, report, False)

    # Validar traduction completa
    report = validate_translation(original_text, translated_units, doc)

    # Se há erros (não apenas avisos), tentar retry das unidades com erro
    if report.errors:
        # Identificar quais unidades causaram erros
        error_units = set()
        for issue in report.errors:
            error_units.update(issue.units)

        # Re-tentar apenas as unidades com erro
        retry_translated: Dict[int, str] = {}
        for idx in error_units:
            if idx in translated_units:
                unit = doc.translatable_units[idx]
                unit_hash = _unit_hash(unit)
                unit_text = translated_units[idx]

                # Retry com o cliente
                re_try = translate_with_retry(unit_text, max_retries=max_retries)
                if re_try is not None:
                    retry_translated[idx] = re_try
                else:
                    # Manter o que tem; validação vai apontar
                    retry_translated[idx] = unit_text

        # Aplicar retry
        if retry_translated:
            doc_after_apply2 = apply_translations(doc, retry_translated)
            try:
                final_text = restore(doc_after_apply2)
            except ValueError:
                final_text = None

            # Validar novamente
            report2 = validate_translation(original_text, {**translated_units, **retry_translated}, doc)
            if not report2.errors:
                report = report2
                final_text = restore(apply_translations(doc, {**translated_units, **retry_translated}))
            else:
                # Ainda com erros após retry — retornar sem sobrescrever original
                return (None, report2, False)
        # Se não houve retry (erro nas unidades já tinha sido mantido original), usar report atual
        # O que já temos em report está adequado

    # Sucesso: validou ok (pode ter avisos, mas não erros)
    success = report.ok

    return (final_text, report, success)


# ---------------------------------------------------------------------------Wrapper: grava em pasta de saída, nunca sobrescreve origem
# ---------------------------------------------------------------------------


def write_translation_output(
    original_path: str,
    final_text: str,
    out_dir: Optional[Path] = None,
    suffix: str = ".mdx",
) -> Optional[Path]:
    """Escreve o documento traduzido em pasta de saída.

    Nunca sobrescreve o arquivo de origem. Gera nome baseado no original com sufixo.
    Se out_dir não informado, usa a pasta out/ da raiz do projeto.
    Recusa gravar se o destino resolvido for igual ao arquivo de origem.

    Args:
        original_path: caminho do arquivo original
        final_text: texto traduzido a ser gravado
        out_dir: pasta de saída. Padrão: out/ na raiz do projeto.
        suffix: sufixo para o nome do arquivo.

    Returns:
        Caminho do arquivo gravado, ou None em erro ou recusa.
    """
    # Usar out/ na raiz do projeto se out_dir não informado
    if out_dir is None:
        _BASE_DIR = Path(__file__).parent
        out_dir = _BASE_DIR / "out"

    orig = Path(original_path).resolve()
    out_path = (out_dir / orig.name).resolve() if out_dir else None

    # Recusa se destino resolvido for igual ao arquivo de origem
    if out_path and out_path == orig:
        print(
            f"[write_translation_output] Recusa: destino resolvido ({out_path}) "
            "é igual ao arquivo de origem ({orig}), não gravando."
        )
        return None

    # Se o arquivo já estiver em out/, não mover (apenas se o out_dir for o do projeto)
    if out_dir is not None and out_dir.is_absolute():
        try:
            # Verificar se o caminho original já está dentro da out_dir
            if orig.is_relative_to(out_dir) or orig.parent.is_relative_to(out_dir):
                print(
                    f"[write_translation_output] Arquivo já em out/, ignorando: {original_path}"
                )
                return None
        except ValueError:
            # Se os caminhos não estão no mesmo drive/raiz, continua normally
            pass

    try:
        # Garantir que a pasta de saída existe
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(final_text)
        print(f"[write_translation_output] Gravado: {out_path}")
        return out_path
    except OSError as e:
        print(f"[write_translation_output] Erro ao gravar: {e}")
        return None


# ---------------------------------------------------------------------------
# Função de conveniência: traduzir um arquivo do disco
# ---------------------------------------------------------------------------

def translate_file_path(
    file_path: str,
    max_retries: int = 2,
    use_cache: bool = True,
) -> Tuple[Optional[str], object, bool]:
    """Traduz um arquivo a partir de seu caminho no disco.

    Lê o arquivo, executa translate_file() e escreve saída em out/.

    Returns:
        (final_text, report, success) — final_text será None se houver falha crítica.
    """
    # Ler original
    try:
        original_text = Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        print(f"[translate_file_path] Erro ao ler {file_path}: {e}")
        report = type("Report", (), {"ok": False, "errors": [], "warnings": [],
                                     "codes": lambda s=None: [], "failed_units": [],
                                     "format": lambda: f"Erro lendo arquivo: {e}"})()
        return (None, report, False)

    # Traduzir
    final_text, report, success = translate_file(
        original_text,
        max_retries=max_retries,
        use_cache=use_cache,
    )

    # Gravando saída (se houver sucesso e há texto)
    if success and final_text:
        write_translation_output(file_path, final_text)

    return (final_text, report, success)