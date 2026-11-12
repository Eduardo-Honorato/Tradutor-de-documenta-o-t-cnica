# tradutor-docs

Tradutor de documentação técnica **EN → PT-BR** para arquivos MDX. Ele traduz a prosa e deixa o resto exatamente como está: blocos de código, código inline, URLs, caminhos, comandos e flags, variáveis, imports, JSX e nomes de produtos.

> **Status:** em desenvolvimento. A suíte de testes (herméticos, sem rede) passa, e a tradução real foi validada nas amostras de `scripts/amostras/`.

## Como funciona

1. **Proteção** (`src/protect.py`): lê o MDX com `markdown-it-py`, separa a prosa (as unidades de tradução) do que não pode ser tocado e troca o conteúdo protegido por placeholders (`⟦P1⟧`, `⟦P2⟧`...).
2. **Tradução** (`src/translate_client.py`, `src/translate.py`): envia cada unidade a uma API de chat compatível com OpenAI. Há retentativas, cache em disco por unidade e uma versão do prompt, então mudar o prompt invalida o cache automaticamente. Títulos iguais recebem a mesma tradução dentro de um arquivo.
3. **Validação** (`src/validate.py`): confere se todos os placeholders voltaram intactos, se a sintaxe dos links foi preservada e se sobrou inglês na tradução.
4. **Gravação**: restaura o conteúdo protegido e grava o resultado. Se alguma unidade falhar na validação, **nada é gravado** para aquele arquivo, o original fica intacto e o motivo aparece no relatório.

## Requisitos

- Python 3 (desenvolvido e testado com Python 3.14 no Windows).
- Uma API de chat compatível com OpenAI. O projeto foi testado com a NVIDIA (`https://integrate.api.nvidia.com/v1`) e o modelo `nvidia/nemotron-3.5-lightning-30b-a3b`.

## Instalação

```powershell
git clone https://github.com/Eduardo-Honorato/Tradutor-de-documenta-o-t-cnica.git
cd Tradutor-de-documenta-o-t-cnica
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

No Linux ou macOS, troque a ativação por `source .venv/bin/activate` e a cópia por `cp .env.example .env`.

## Configuração

Preencha o `.env` (ele está no `.gitignore` e **nunca** deve ser commitado):

| Variável | O que é |
|---|---|
| `TRANSLATOR_BASE_URL` | URL base da API compatível com OpenAI (o cliente acrescenta `chat/completions`) |
| `TRANSLATOR_MODEL` | Nome do modelo |
| `TRANSLATOR_API_KEY` | Chave da API |

## Uso

### Traduzir uma pasta

```powershell
python scripts/traduzir.py <pasta_entrada> [--saida PASTA] [--sem-cache] [--max-retries N]
```

- Percorre a pasta de entrada, em ordem alfabética, um arquivo por vez.
- Preserva a estrutura de subpastas na saída (padrão: `out_pasta/` na raiz do projeto). Dois arquivos com o mesmo nome em pastas diferentes não colidem.
- Recusa uma saída igual à pasta de entrada ou dentro dela, e nunca sobrescreve um arquivo de origem.
- Um erro em um arquivo não derruba os outros: a exceção é registrada e o processo segue.
- No fim, imprime um resumo (total, traduzidos, falhas, exceções, tempo e os arquivos não gravados, com o motivo).
- Códigos de saída: `0` tudo certo, `1` algum arquivo falhou ou deu exceção, `2` erro de uso.

### Traduzir um arquivo e ver o diagnóstico

```powershell
python scripts/verificar.py scripts/amostras/quick-start-en.mdx
```

Mostra o resultado, o tempo, se a origem ficou inalterada e se código inline, URLs e blocos de código foram preservados.

### Conferir a qualidade das saídas

`scripts/checar_qualidade.py` confere a qualidade das traduções gravadas (por exemplo, título e H1 iguais e inglês remanescente).

## Testes

```powershell
pip install pytest   # se ainda não estiver instalado
python -m pytest -q
```

Os testes são herméticos: não precisam de rede nem de `.env`. O `tests/corpus/` reúne os arquivos MDX usados para exercitar a proteção e a validação, e `scripts/amostras/` tem quatro amostras em inglês para a tradução real.

## Segurança

- A chave fica só no `.env`, que está no `.gitignore`. O repositório versiona apenas o `.env.example`, com valores vazios.
- Tudo que o tradutor imprime (exceções, corpo de erro HTTP, relatórios) passa por `redact_secrets`, que troca a chave por `***` **antes** de qualquer truncamento.
- Os testes de redação usam apenas chaves falsas.

## Limitações conhecidas

- A saída de um LLM varia: a mesma frase pode sair com pequenas diferenças, e termos como *Endpoint* podem aparecer de formas diferentes entre trechos.
- Nas amostras, cada arquivo levou de 7 a 35 segundos, e chamadas que passam de 30 segundos são repetidas.
- Só EN → PT-BR e só arquivos `.mdx`.
- Os arquivos são processados em sequência, sem paralelismo.

## Licença

Ainda não definida.
