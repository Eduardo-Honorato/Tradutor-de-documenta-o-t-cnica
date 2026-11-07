import os
import shutil

import pytest

from src.translate import _translation_cache
from src.translate_client import CACHE_DIR, _ensure_cache_dir, _os_cache_get, _os_cache_set


@pytest.fixture(autouse=True, scope="function")
def isolate_cache(tmp_path):
    """Isola o diretório de cache para cada teste usando tmp_path.

    O fixture:
    1. Faz backup do cache real (se existir).
    2. Redefine CACHE_DIR para tmp_path do teste.
    3. Reinicia o dict _translation_cache de translate.py para estado limpo.
    4. Após o teste, restaura o cache real.
    """
    # Backup do cache real
    real_cache_dir = CACHE_DIR
    real_cache_backup = os.path.join(tmp_path, ".translate_cache_backup")

    # Move o cache real para o backup (se existir)
    if os.path.exists(real_cache_dir):
        shutil.copytree(real_cache_dir, real_cache_backup)

    # Redefine CACHE_DIR para o tmp_path do teste
    # Atribuímos diretamente no módulo para que as funções usem o novo caminho
    import src.translate_client as tc_mod
    tc_mod.CACHE_DIR = str(tmp_path / ".translate_cache")
    _ensure_cache_dir(str(tmp_path / ".translate_cache"))

    # --- Novo: resetar o dict global de cache de translate.py ---
    # Assim cada teste tem cache isolado e ninguém "vaza" traduções para o outro
    from src.translate import _translation_cache
    _translation_cache.clear()
    # Redireciona o _CACHE_DIR de translate.py para o tmp_path
    import src.translate as t_mod
    t_mod._CACHE_DIR = tmp_path / ".translate_cache"
    # --- fim do reset ---

    yield

    # Pós-teste: restaura o cache real
    # Remove o cache temporário
    if os.path.exists(tmp_path / ".translate_cache"):
        shutil.rmtree(tmp_path / ".translate_cache")

    # Restaura o original
    if os.path.exists(real_cache_backup):
        # Remove o dir temporário se ainda existir
        shutil.rmtree(tmp_path / ".translate_cache", ignore_errors=True)
        # Restaura copiando de volta
        if os.path.exists(real_cache_backup):
            # Recria o dir original se não existir
            if not os.path.exists(real_cache_dir):
                os.makedirs(real_cache_dir, exist_ok=True)
            # Copia de volta os arquivos
            for item in os.listdir(real_cache_backup):
                s = os.path.join(real_cache_backup, item)
                d = os.path.join(real_cache_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            # Remove o backup
            shutil.rmtree(real_cache_backup, ignore_errors=True)

    # Resetar o dict de cache de translate.py novamente após o teste
    from src.translate import _translation_cache
    _translation_cache.clear()