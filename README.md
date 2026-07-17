# Nimrod

Versão enxuta do Sentinela: **1 perfil por vez, sem fila, sem Supabase, sem orquestração 24/7.**
Roda como script síncrono do começo ao fim e entrega um PDF completo do perfil analisado.

## Por que existe

O Sentinela original resolve o problema de monitorar **muitos** perfis continuamente — e para isso
precisa de fila atômica (Supabase `SELECT FOR UPDATE SKIP LOCKED`), pool de sessões, SRE autônomo,
DOM healing, watchdog etc. Essa complexidade é a fonte da maior parte da fragilidade de scrape/API/escala.

O Nimrod resolve um problema menor e mais comum: *"quero o dossiê completo deste perfil, agora"*.
Sem fila para travar, sem múltiplas sessões brigando por lock, sem infraestrutura para manter no ar.

## O que foi reaproveitado do Sentinela (código real, não reescrito)

| Módulo Nimrod | Origem | O que faz |
|---|---|---|
| `core/instagram_scraper_v2.py` | `core/instagram_scraper_v2.py` | Scraping via API interna do IG + fallback DOM. **Já ignora posts fixados nativamente** (`is_pinned` → skip). Removida só a dependência de Supabase no carregamento de sessão (agora 100% `.env`). |
| `core/ai_service.py` | `core/ai_service.py` | Cascata de classificação PASA (ollama → mistral → groq/gemini/claude → fallback). Sem alterações — já era desacoplado do Supabase. |
| `core/behavior_engine.py` | `core/behavior_engine.py` | Detecção de comentários coordenados/bots via LLM. Sem alterações (a persistência em Supabase que ele tenta fazer é best-effort e falha silenciosamente sem quebrar o resto). |
| `core/stanza_nlp.py` | `core/stanza_nlp.py` | Lematização, POS tagging, n-gramas periciais. Sem alterações. |
| `core/lexical_filter.py`, `core/circuit_breaker.py`, `core/exceptions.py` | idem | Dependências do `ai_service`/scraper. Copiados sem alteração. |
| `core/clustering.py` | `processing/data_miner.py` | **Reescrito** como funções puras (sem `BaseWorker`/fila/DB) operando em memória sobre a lista de comentários já coletada. |
| `report/pdf_builder.py` | `processing/report_generator.py` | Evolução do gerador original: mantém capa, cards de evidência e selo de integridade, mas troca os retângulos manuais por **gráficos matplotlib reais** e adiciona **tabelas** (autores, n-gramas) e **seções novas** (temas, comportamento coordenado). |

## O que foi descartado (de propósito)

- Fila Supabase / `queue_manager.py` / locks atômicos entre workers
- Pool de múltiplas sessões rotacionando sob concorrência / `session_manager.py`
- Watchdog, SRE autônomo, cronjobs de autocura
- Mineração de rede entre múltiplos perfis (`sa_mineracao_redes.py`)
- Módulo financeiro (`treasurer_worker.py`)
- Orquestrador multi-worker (`main_runner.py`)

## Arquitetura

```
nimrod/
├── nimrod.py                    # CLI: python nimrod.py @perfil
├── config.py                    # Configuração via .env (sem Supabase)
├── pipeline.py                  # Orquestra: coleta -> IA -> linguística -> comportamento -> clustering
├── core/
│   ├── instagram_scraper_v2.py  # Scraper (reaproveitado, sessão só via .env)
│   ├── ai_service.py            # Cascata de classificação PASA (reaproveitado)
│   ├── behavior_engine.py       # Detecção de coordenação/bots (reaproveitado)
│   ├── stanza_nlp.py            # Linguística forense (reaproveitado)
│   ├── clustering.py            # Clusterização temática (reescrito, sem fila/DB)
│   ├── lexical_filter.py        # Filtro de ruído textual (reaproveitado)
│   ├── circuit_breaker.py       # Proteção contra falhas em cascata de IA (reaproveitado)
│   ├── exceptions.py            # Exceções customizadas (reaproveitado)
│   └── sentinela_ai_config.py   # Fallback providers de IA (reaproveitado)
├── config_data/
│   ├── fallback_providers.yaml  # Lista de provedores de IA de reserva
│   └── custom_rules.json        # Regras extras de classificação
├── report/
│   ├── charts.py                 # Gráficos matplotlib (donut, barras, timeline, gauge)
│   └── pdf_builder.py             # Monta o PDF final (capa, KPIs, tabelas, evidências, selo)
└── output/                       # PDFs gerados: nimrod_@perfil_AAAA-MM-DD_HHMM.pdf
```

## Fluxo de execução

```
python nimrod.py @perfil
```

1. **Coleta** — 1 sessão, varre posts do mais recente pro mais antigo, `FAST-SKIP` em fixados,
   para em `max_posts` ou quando os posts ficam mais velhos que `max_age_days`.
2. **Classificação** — cada comentário passa pela cascata PASA (categoria + `is_hate` + confiança + parecer).
3. **Linguística** — Stanza extrai lemas e n-gramas periciais (bigramas/trigramas) do corpus.
4. **Comportamento** — `BehaviorEngine` identifica clusters de comentários coordenados (mesmo núcleo
   argumentativo, múltiplas contas, mesma janela de tempo).
5. **Temas** — `clustering.py` agrupa os comentários hostis por tema semântico (TF-IDF ou GloVe se
   disponível localmente).
6. **Relatório** — `pdf_builder.py` monta o PDF: capa com gauge de severidade → resumo executivo com
   KPIs e donut → volume por post + timeline → top autores hostis (gráfico + tabela) → n-gramas
   periciais (tabelas) → temas identificados → comportamento coordenado (se detectado) → evidências
   detalhadas → selo de integridade SHA-256.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
python -c "import stanza; stanza.download('pt')"   # 1x, baixa o modelo em português

cp .env.example .env
# preencha INSTAGRAM_SESSIONID e ao menos 1 provedor de IA (ex.: MISTRAL_API_KEY)

python nimrod.py @perfil
```

## Garantia de integridade de dados (regra imutável)

**Nenhum relatório do Nimrod pode conter dado mockado, sintético, de exemplo ou fabricado
se passando por dado real.** Isso não é só uma diretriz de código — é uma trava técnica em
3 camadas, implementada em `core/integrity.py`:

1. **Proveniência obrigatória na origem.** O único ponto do scraper que constrói um registro
   final de comentário (`core/instagram_scraper_v2.py`, dentro de `_scrape_post`) grava a tag
   `"fonte_dado": "coleta_real_instagram"`. Nenhum outro caminho do código gera essa tag.
2. **Pré-checagem antes de abrir o navegador.** `pipeline.py` recusa rodar se não houver sessão
   real do Instagram (`INSTAGRAM_SESSIONID`) ou nenhum provedor de IA configurado — falhar cedo
   e alto é melhor que produzir um relatório com lacunas silenciosas.
3. **`assert_dados_reais()` em 2 pontos independentes.** Chamado no fim do `pipeline.py` e de
   novo dentro de `report/pdf_builder.py` (defesa em profundidade — mesmo se alguém chamar o
   gerador de PDF direto, pulando o pipeline). Qualquer comentário sem a tag de proveniência
   levanta `DadosNaoReaisError` e **nenhum arquivo é escrito em disco**.

Além disso:

- **Falha de classificação de IA nunca vira "NEUTRO" silenciosamente.** Se todos os provedores
  da cascata PASA falharem para um comentário, ele é marcado como `ERRO_CLASSIFICACAO` — uma
  categoria própria, visível no gráfico de distribuição, excluída das contagens de discurso
  hostil (não conta nem como hostil, nem como seguro). `assert_classificacao_confiavel()` aborta
  a geração se mais de 30% dos comentários caírem em erro (sinal de que a IA está mal configurada).
- **Toda etapa opcional (linguística, comportamento coordenado, clustering) declara seu status**
  (`ok` / `erro: <motivo>` / `desativado`) no próprio relatório, na seção "Nota de Integridade
  dos Dados" — nunca finge ter rodado quando falhou.
- Isso foi testado neste projeto: uma lista de comentários no mesmo formato usado para o teste
  visual do layout (sem a tag de proveniência) foi passada propositalmente ao gerador de PDF e
  foi **bloqueada com `DadosNaoReaisError`**, sem criar nenhum arquivo — comprovando que a trava
  funciona mesmo contra dado com a "cara" de dado real.

## Testado nesta sessão

O gerador de relatório (`report/pdf_builder.py` + `report/charts.py`) foi validado ponta a ponta com
dados sintéticos (180 comentários, 6 posts, clusters de bots simulados) — capa, KPIs, donut, volume
por post, timeline, top autores, temas e comportamento coordenado renderizaram corretamente em PDF.
O scraping real (Playwright + sessão do Instagram) e a cascata de IA (chamadas reais aos provedores)
ainda não foram testados ponta a ponta neste ambiente, pois dependem de credenciais e acesso de rede
ao Instagram — validar com uma sessão real antes do primeiro uso em produção.

## Próximos passos sugeridos

1. Rodar com uma sessão real do Instagram + 1 provedor de IA configurado, num perfil de teste pequeno.
2. Ajustar `NIMROD_MAX_POSTS`/`NIMROD_MAX_COMMENTS_PER_POST` conforme o volume real de comentários.
3. Se quiser, plugar notificação via `ntfy` (já vem pronto em `nimrod.py`, só setar `NTFY_TOPIC`).
