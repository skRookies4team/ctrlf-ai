from app.metrics.prometheus import AI_STRATEGY
from app.strategy.state import LAST_STRATEGY

async def decide_strategy(domain: str):
    # -------------------------
    # 전략 결정 로직 (예시)
    # -------------------------
    if domain == "HR":
        new_strategy = {
            "disable_rag": True,
            "model": "quality-gate",
            "reason": "FAST_NO_RAG",
        }
    else:
        new_strategy = {
            "disable_rag": False,
            "model": None,
            "reason": "DEFAULT",
        }

    # -------------------------
    # 🔔 변경 감지
    # -------------------------
    old_strategy = LAST_STRATEGY.get(domain)

    if old_strategy != new_strategy:
        # 🔥 Prometheus 메트릭 갱신
        AI_STRATEGY.labels(
            domain=domain,
            strategy=new_strategy["reason"]
        ).set(1)

        LAST_STRATEGY[domain] = new_strategy

    return new_strategy
