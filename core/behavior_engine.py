import logging
import json
import random
from typing import List, Dict, Any

from core.ai_service import ai_service

logger = logging.getLogger("core.behavior_engine")


class BehaviorEngine:
    """
    Módulo Solenya (v95.0) - Agente de Inteligência e Disinformation Analysis.
    Substituiu a contagem burra de N-Gramas (SpaCy) por Avaliação Semântica Larga
    via LLMs para detectar campanhas coordenadas de desinformação através do contexto.
    """

    def __init__(self):
        pass

    async def detect_coordinated_clusters(self, comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Análise OODA Híbrida:
        Observe: Coleta lote de comentários curtos em intervalo curto de tempo.
        Orient: Reúne textos para análise de lote.
        Decide: Consulta à IA de triagem rápida se os discursos possuem mesma intenção sintática dissimulada.
        Act: Etiqueta o cluster.
        """
        if len(comments) < 3:
            return comments

        try:
            from core.ai_service import ContextClassifier
        except ImportError:
            ContextClassifier = None

        # Prepara Lote Textual
        batch_text = ""
        index_map = {}
        
        # Filtra comentários com contextos claramente positivos
        valid_comments = []
        for c in comments:
            txt = c.get("texto_bruto", "").strip()
            if len(txt) > 5:
                # Se ContextClassifier estiver disponível e detectar contexto positivo, ignora o comentário
                if ContextClassifier and ContextClassifier.is_positive_context(txt):
                    continue
                valid_comments.append(c)

        if len(valid_comments) < 3:
            return comments

        for idx, c in enumerate(valid_comments):
            txt = c.get("texto_bruto", "").strip()
            batch_text += f"[{idx}] {txt}\n"
            index_map[str(idx)] = c

        if not batch_text:
            return comments

        prompt = f"""
        Você é o "Solenya", o Analista Chefe de Desinformação do Sentinela.
        Sua missão é ler um lote de comentários de redes sociais extraídos no mesmo minuto e determinar se há um Ataque Coordenado (Swarm/Milícia Digital).
        
        As campanhas coordenadas modernas não copiam e colam o mesmo texto exato; eles usam sinônimos para o mesmo núcleo argumentativo ou narrativa estrutural.
        Leia os comentários abaixo e agrupe pelo Índice (IDs entre colchetes) aqueles que compartilham EXATAMENTE a mesma métrica argumentativa estruturada, não apenas a mesma opinião política.
        
        LOTE DE COMENTÁRIOS:
        {batch_text}
        
        Retorne um JSON estrito no seguinte formato:
        {{
            "clusters": [
                {{
                    "ids": ["0", "2", "5"],
                    "narrative_core": "Narrativa identificada que une os textos",
                    "confidence": 0.9
                }}
            ]
        }}
        Se não houver coordenação de no mínimo 3 IDs no mesmo cluster, retorne "clusters": [].
        """
        try:
            # Garante que os clients de IA do ai_service foram inicializados
            ai_service._ensure_clients()
            
            # Invoca modelo de triagem veloz (Mistral/Flash)
            response = await ai_service.mistral_client.chat.completions.create(
                model="open-mistral-nemo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            data = json.loads(response.choices[0].message.content)

            clusters = data.get("clusters", [])
            for cl in clusters:
                ids = cl.get("ids", [])
                if len(ids) < 3:
                    continue

                cluster_id = f"solenya_cog_{random.randint(1000, 9999)}"
                logger.info(f"🤖 [Solenya] Swarm Semântico Detectado! IDs: {ids} | Núcleo: {cl.get('narrative_core')}")

                for string_id in ids:
                    if string_id in index_map:
                        ref = index_map[string_id]
                        ref["is_bot"] = True
                        ref["bot_pattern"] = "COORDENACAO_SEMANTICA"
                        ref["cluster_id"] = cluster_id
                        ref["cluster_size"] = len(ids)
                        ref["slogans_detectados"] = [cl.get("narrative_core")]

                db_ids = [
                    index_map[sid].get("id")
                    for sid in ids
                    if sid in index_map and index_map[sid].get("id")
                ]

                # --- PERSISTÊNCIA CRÍTICA (PASA v98.6) ---
                # Isolada em seu próprio try/except, SEM dependência da telemetria.
                # Bug corrigido (v98.5 -> v98.6): antes, este update vinha DEPOIS
                # do insert de telemetria dentro do MESMO try/except. Se o insert
                # em telemetry_events falhasse (RLS, rede, schema desalinhado),
                # o except capturava e o update em `comentarios` NUNCA executava
                # — uma falha cosmética de telemetria silenciosamente impedia a
                # marcação real do cluster (is_bot/cluster_id), que é o dado que
                # o produto de fato depende. Telemetria nunca deve poder
                # bloquear a operação principal.
                if db_ids:
                    try:
                        from core.supabase_service import get_supabase_client
                        db = get_supabase_client()
                        db.table("comentarios").update({
                            "is_bot": True,
                            "bot_pattern": "COORDENACAO_SEMANTICA",
                            "cluster_id": cluster_id
                        }).in_("id", db_ids).execute()
                    except Exception as e_persist:
                        # Esta falha é grave (dado real não foi marcado) e
                        # merece log de erro alto, mas ainda não deve abortar
                        # o loop — outros clusters do mesmo lote devem seguir
                        # sendo processados independentemente.
                        logger.error(
                            f"[Solenya] FALHA CRÍTICA ao persistir cluster {cluster_id} "
                            f"em comentarios (dado NÃO marcado como bot): {e_persist}"
                        )

                # --- TELEMETRIA (PASA v98.6) ---
                # Bloco independente e descartável: se falhar, não afeta o
                # dado real persistido acima. Roda depois de propósito, para
                # garantir que a operação crítica já foi tentada primeiro.
                try:
                    from core.telemetry import emit_telemetry
                    emit_telemetry(
                        event_type="cluster_detected",
                        source_module="behavior_engine",
                        status="success",
                        metadata={
                            "cluster_id": cluster_id,
                            "size": len(ids),
                            "narrative_core": cl.get("narrative_core"),
                            "comment_ids": db_ids,
                        },
                    )
                except Exception as e_telemetry:
                    logger.warning(
                        f"[Solenya] Falha ao registrar cluster_detected na telemetria "
                        f"(cluster {cluster_id} já foi persistido normalmente): {e_telemetry}"
                    )

        except Exception as e:
            logger.error(f"[Solenya] Erro na inferência cognitiva de clusters: {e}")

        return comments


behavior_engine = BehaviorEngine()