# tradutor-docs

Tradutor de documentação técnica.

## Stack

- Python + markdown-it-py + pytest
- Motor de tradução: proxy NVIDIA do fcc-claude, atrás de uma interface Translator (configuração via .env).

## Idiomas

- EN -> PT-BR

## Regras

- Nunca escrever em `input/`.
- Todo código novo tem teste; rodar `pytest` antes de dar uma etapa como concluída.
- Trabalhar uma etapa por vez, um commit por etapa.
- Formato: MDX.

## O que NÃO traduzir (blocos e inline)

- Blocos de código (inclusive comentários)
- Código inline
- URLs
- Caminhos
- Comandos/flags
- Variáveis
- Nomes de produtos
- import/export
- Componentes JSX e suas props
- Expressões `{...}`

## O que traduzir

- Prosa
- Títulos
- Alt text
- Células de tabela

## Títulos

- Títulos traduzidos mantêm o id/âncora original para não quebrar links internos.

## Motor

- Proxy NVIDIA do fcc-claude, atrás de uma interface `Translator`.
- Configuração via `.env` (URL base e modelo).
- `.env.example` contém as variáveis sem valores secretos.