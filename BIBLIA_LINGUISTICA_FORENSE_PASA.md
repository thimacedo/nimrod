# 📘 BÍBLIA LINGUÍSTICA FORENSE (PASA v16.4)

Este documento constitui a Bússola Moral e Linguística definitiva do **Sentinela Democrática**. Ele é o alicerce interpretativo para todos os Workers, Agentes e LLMs (Gemini, Groq, Ollama) envolvidos na classificação de discursos.

**Objetivo:** Eliminar falsos positivos (sarcasmo/ironia/apoio agressivo) e garantir precisão forense e criminal na tipificação do discurso de ódio e da violência política no Brasil.

---

## 1. O PARADOXO DA IRONIA E DO SARCASMO

A ironia é o cemitério das IAs ingênuas. Para o Sentinela não se comportar como um "Jerry", a IA deve analisar a **valência inversa**. (*Referência: Journal of Sensors - 2022 - Sarcasm Analysis for Twitter Data*).

### 1.1. Regras de Detecção de Sarcasmo
- **A Elogiosa Destruição**: Se o texto exalta uma qualidade absurda ou impossível atrelada a uma crítica óbvia, **É SARCASMO**. Não deve ser classificado como "NEUTRO" se o contexto final for um insulto velado.
  - *Exemplo*: "Nossa, que gênio da economia, só faliu três empresas! O Nobel vem aí!"
  - *Decisão Forense*: Isso é um **INSULTO_AD_HOMINEM** disfarçado.
- **O Falso Alerta de Violência (Hype Positivo)**:
  - *Exemplo*: "Esse cara é foda, a proposta dele é uma bomba de boa! Matou a pau no debate!"
  - *Decisão Forense*: A presença de "bomba" e "matou" não aciona `VIOLENCIA_FISICA`. O contexto sintático exprime um **Elogio**. Deve ser classificado como **NEUTRO** (pois não é ódio, é engajamento positivo).
- **Emoticons como Modificadores Semânticos**: A ironia online frequentemente usa 🤡, 🙄, ou 🙃. O modelo deve inverter a polaridade da frase se ela for seguida por emojis de deboche.

---

## 2. O ESPECTRO DA AGRESSIVIDADE: APOIADOR vs. INFRATOR

Nem todo mundo que grita é um criminoso. Há uma diferença colossal entre a polarização democrática e o crime de ódio estruturado (*Referência: Monitoramento de Discurso de Ódio e Violência*).

### 2.1. O Apoiador Agressivo / Passional (Falso Positivo)
- **Perfil**: Defende o seu candidato com unhas e dentes. Pode usar linguagem chula ou agressiva direcionada à "situação", mas não tipifica crime de ódio.
- *Exemplo*: "Vocês são muito burros de não ver que o Daciolo é o único que presta! Esse governo atual é um lixo!"
- *Decisão Forense*: **NEUTRO**. É xingamento político genérico, não é ódio identitário, nem ameaça à integridade física.

### 2.2. A Crítica Ácida vs. A Desinformação Criminosa
- **Crítica Política**: "Esse prefeito é um incompetente, a cidade tá cheia de buracos." -> **NEUTRO**.
- **Rigor Criminal (Imputação de Crime)**: "Esse prefeito é um ladrão corrupto que desviou dinheiro da merenda." -> **RIGOR_CRIMINAL**. (Acusação de crime sem provas no contexto de difamação eleitoral).

---

## 3. LEXICOLOGIA DO ÓDIO: VETORES E CATEGORIAS (PASA v16.4)

Baseado nos relatórios da UFSCar, NetLab e SaferNet, os LLMs devem estar hiper-calibrados para os seguintes eixos:

### A. Xenofobia Regionalizada (O Efeito Calendário)
A palavra "nordestino" atrai ódio sazonal em época de eleição.
- **Termos-Gatilho**: "Pobre", "analfabeto", "ingrato", "burro", "miserável", "não sabe votar", "cabeça chata", "escorados", "Bolsa Família".
- *Decisão Forense*: O uso desses termos associados à origem geográfica é **ODIO_IDENTITARIO** (Xenofobia) de Severidade ALTA ou CRÍTICA.

### B. A "Machosfera" e a Violência de Gênero
Candidatas femininas sofrem ataques no corpo e na moral.
- **Termos-Gatilho**: Xingamentos de cunho sexual (v*dia, p*ta, mal amada), ameaças de estupro, "lugar de mulher é na cozinha", vazamento/ameaça de vazamento de conteúdo íntimo (Revenge Porn), referências "redpill".
- *Decisão Forense*: Trata-se de **VIOLENCIA_GENERO**. Severidade CRÍTICA. O gênero está sendo utilizado como arma de guerra política.

### C. Racismo Religioso e Narcopentecostalismo
A demonização de religiões de matriz africana.
- **Termos-Gatilho**: "Macumbeira", "magia negra", "invocando o diabo", "terreiro", associação de orixás a entidades malignas no contexto eleitoral.
- *Decisão Forense*: Não é mero "insulto". É **ODIO_IDENTITARIO** (Racismo Religioso).

### D. Ataque Institucional e Milícias Digitais
Discurso coordenado para corroer as bases da democracia.
- **Termos-Gatilho**: "O STF tem que ser fechado", "fraude nas urnas", "vamos invadir o congresso", "Alexandre de Moraes ditador".
- *Decisão Forense*: **ATAQUE_INSTITUCIONAL**.

---

## 4. INSTRUÇÕES PARA PROMPTING DE IA (SYSTEM PROMPTS)

Para evitar alucinações e proteger a taxonomia, todo LLM que ler este documento deve operar sob as seguintes restrições lógicas:

1. **A Regra do "So What?" (E Daí?)**: Se um comentário ofende, mas o "alvo" da ofensa é o vento, o sistema, ou um conceito abstrato (ex: "Eu odeio impostos, que raiva desse país!"), classifique como **NEUTRO**. Apenas ofensa DIRECIONADA a uma pessoa ou minoria gera alerta.
2. **Contexto é Rei**: "Mato e morro por esse candidato!" = Expressão de lealdade (NEUTRO). "Mato e morro se esse candidato não ganhar, vou invadir o TSE!" = Ameaça à Democracia (**ATAQUE_INSTITUCIONAL**).
3. **Escala de Severidade (0 a 100)**:
   - **0 a 20 (BAIXA)**: Ironias sutis, insultos levianos ("chato", "feio").
   - **30 a 60 (MÉDIA)**: Discurso de ódio genérico, insultos direcionados pesados (Rigor Criminal leve).
   - **70 a 90 (ALTA)**: Xenofobia estrutural, racismo explícito, misoginia.
   - **91 a 100 (CRÍTICA)**: Ameaça de morte, incitação ao terrorismo, estupro, apologia ao nazismo.

---
*Este documento é o núcleo do Sentinela Democrática. Não modifique sem a aprovação do Arquiteto-Chefe (Pickle Rick).*
