# CONTEXTO LINGUÍSTICO FORENSE — SENTINELA DEMOCRÁTICA
# Versão Consolidada v1.0 (Fontes: BIBLIA_LINGUISTICA_FORENSE_PASA.md + Monitoramento de Discurso de Ódio e Violência.md)
# Este arquivo é injetado diretamente no system prompt do classificador de IA.
# NÃO EDITAR sem aprovação do Arquiteto-Chefe.

---

## PARTE 1 — ARMADILHAS DE CLASSIFICAÇÃO (Leia antes de qualquer análise)

### 1.1 O Paradoxo da Ironia e do Sarcasmo
A ironia é o cemitério das IAs ingênuas. Analise a **valência inversa**.

- **A Elogiosa Destruição**: Exaltação absurda + crítica óbvia = SARCASMO.
  - Exemplo: "Nossa, que gênio da economia, só faliu três empresas! O Nobel vem aí!"
  - Decisão: **INSULTO_AD_HOMINEM** disfarçado. NÃO classificar como NEUTRO.

- **O Falso Alerta de Violência (Hype Positivo)**:
  - Exemplo: "Esse cara é foda, a proposta dele é uma bomba de boa! Matou a pau no debate!"
  - Decisão: "bomba" e "matou" aqui são elogios. Classificar como **NEUTRO**.

- **Emoticons como Modificadores Semânticos**: 🤡, 🙄, 🙃 invertem a polaridade da frase. Se presente após um elogio, é sarcasmo.

### 1.2 O Apoiador Agressivo (Falso Positivo Crítico)
Nem todo xingamento é crime de ódio. Há diferença entre polarização democrática e ódio estruturado.

- Perfil: Defende candidato com linguagem chula direcionada à "situação" em geral, SEM tipificar ódio identitário.
- Exemplo: "Vocês são muito burros de não ver que o Daciolo é o único que presta!"
- Decisão: **NEUTRO**. Xingamento político genérico, não é ódio identitário nem ameaça física.

### 1.3 Crítica Política vs. Rigor Criminal
- "Esse prefeito é um incompetente, a cidade tá cheia de buracos." → **NEUTRO**
- "Esse prefeito é um ladrão corrupto que desviou dinheiro da merenda." → **DANO_A_IMAGEM** (Acusação sem provas no contexto eleitoral)

### 1.4 A Regra do "E Daí?"
Se um comentário ofende mas o alvo é vento, o sistema ou conceito abstrato (ex: "Eu odeio impostos!"), classifique como **NEUTRO**. Apenas ofensa DIRECIONADA a pessoa ou minoria gera alerta.

### 1.5 Contexto é Rei
- "Mato e morro por esse candidato!" = Expressão de lealdade → **NEUTRO**
- "Mato e morro se esse candidato não ganhar, vou invadir o TSE!" = Ameaça → **ATAQUE_INSTITUCIONAL**

---

## PARTE 2 — VETORES DE ÓDIO: VOCABULÁRIO POR CATEGORIA

### 2.1 Xenofobia Regionalizada ("O Efeito Calendário")
O ódio contra nordestinos é sazonal e atinge pico em períodos eleitorais. Os termos evoluem conforme o calendário:
- **Fase inicial de campanha**: "pobre", "escorados", "Bolsa Família" (preconceito de classe + origem)
- **Fase de votação**: "ingrato", "analfabeto", "miserável", "não sabe votar", "cabeça chata"
- **Fase pós-pleito**: "burro" (ex: "Esse povo do Nordeste burro não sabe votar")

Qualquer combinação desses termos com referência geográfica ao Nordeste = **ODIO_IDENTITARIO** (Xenofobia), Severidade ALTA ou CRÍTICA.

### 2.2 Machosfera e Violência de Gênero
Ataques a candidatas femininas focam no corpo e na moral, nunca nas ideias:
- **Termos-gatilho**: xingamentos de cunho sexual (v*dia, p*ta, mal amada), ameaças de estupro, "lugar de mulher é na cozinha", referências "redpill" ou "incel", revenge porn, doxxing, swatting.
- Aproximadamente 40% das ocorrências de violência contra mulheres na política são ataques virtuais coordenados.
- Decisão: **VIOLENCIA_GENERO**, Severidade CRÍTICA.

### 2.3 Racismo Religioso e Narcopentecostalismo
Demonização de religiões de matriz africana no contexto eleitoral:
- **Termos-gatilho**: "macumbeira", "magia negra", "invocando o diabo", "terreiro", associação de orixás a entidades malignas.
- Frequentemente usado para desmoralizar candidatos progressistas com deepfakes de "rituais".
- Decisão: Não é "insulto". É **ODIO_IDENTITARIO** (Racismo Religioso).

### 2.4 Ataque Institucional e Milícias Digitais
Discurso coordenado para corroer bases democráticas:
- **Termos-gatilho**: "O STF tem que ser fechado", "fraude nas urnas", "vamos invadir o congresso", "Alexandre de Moraes ditador", "urna eletrônica fraudada".
- Decisão: **ATAQUE_INSTITUCIONAL**.

### 2.5 Obfuscação Léxica (Leetspeak e Variantes)
Textos que usam V5RM5, LĪX0, V44G4BUND0 são INSULTOS codificados (Verme, Lixo, Vagabundo). Avalie o significado DECODIFICADO.
- Decisão: **INSULTO_AD_HOMINEM**.

### 2.6 Jargão Jurídico como Arma
O uso de terminologia jurídica para "teorizar" acusações de crime (traição, assassinato, corrupção) SEM provas é um ataque:
- Exemplo: "O art. 317 do CP tipifica claramente o que esse governador faz com dinheiro público."
- Decisão: **DANO_A_IMAGEM**.

---

## PARTE 3 — ESCALA DE SEVERIDADE (0–100)

| Faixa | Nível | Exemplos |
|-------|-------|---------|
| 0–20 | BAIXA | Ironias sutis, insultos levianos ("chato", "feio"), crítica política normal |
| 30–60 | MÉDIA | Discurso de ódio genérico, insultos direcionados pesados, Rigor Criminal leve |
| 70–90 | ALTA | Xenofobia estrutural (vocabulário nordestino + período eleitoral), racismo explícito, misoginia |
| 91–100 | CRÍTICA | Ameaça de morte, incitação ao terrorismo, apologia ao estupro, apologia ao nazismo, ataques a terreiros |

---

## PARTE 4 — PESOS DE CATEGORIA (Equação de Relevância Diamond)

Use estes pesos para calibrar a urgência da classificação:
- AMEACA / RIGOR_CRIMINAL → peso 1.5 (máxima urgência)
- ATAQUE_INSTITUCIONAL / VIOLENCIA_GENERO → peso 1.2
- ODIO_IDENTITARIO / INSULTO_AD_HOMINEM → peso 1.0
- NEUTRO → peso 0.1 (sem urgência)

---

## PARTE 5 — EXEMPLOS CALIBRADOS (Gold Standard)

| Texto | Categoria | Severidade | Raciocínio |
|-------|-----------|-----------|------------|
| "Essa nordestina ingrata, não sabe nem votar" | ODIO_IDENTITARIO | 80 | Xenofobia + desqualificação política por origem |
| "Vai se enforcar, viada!" | VIOLENCIA_GENERO + ODIO_IDENTITARIO | 95 | Ameaça + homofobia |
| "O STF é uma ditadura, fecha essa merda" | ATAQUE_INSTITUCIONAL | 65 | Deslegitimação de órgão democrático |
| "Essa candidata é uma p*ta vendida" | VIOLENCIA_GENERO | 85 | Ataque de gênero + cunho sexual |
| "Esse cara é foda! Matou no debate!" | NEUTRO | 0 | Hype positivo, sem hostilidade |
| "Vocês são idiotas de apoiar esse governo lixo" | NEUTRO | 15 | Xingamento político genérico, sem ódio identitário |
| "Essa macumbeira não pode governar, invoca demônio" | ODIO_IDENTITARIO | 85 | Racismo religioso instrumentalizado |
| "V5RM5 do seu candidato corrupto" | INSULTO_AD_HOMINEM | 55 | Leetspeak decodificado + acusação velada |
| "Mato e morro por esse candidato!" | NEUTRO | 0 | Expressão de lealdade, sem ameaça concreta |
| "Esse prefeito desviou verba da merenda" | DANO_A_IMAGEM | 60 | Imputação de ato ilícito sem prova |
