import os
import shutil
import json

def sync_breakthroughs():
    """
    将实验室 (research/) 验证成功的“新尾翼”（模型/参数）
    同步到 F1 赛车 (backend/)。
    """
    print("🏎️  F1 Bridge: Syncing breakthroughs from Lab to Production...")
    
    # 1. 定义源和目标
    RESEARCH_MODELS = "research/src/footy/models"
    PROD_CORE = "backend/core"
    PROD_WEIGHTS = "backend/data/weights/research"
    
    os.makedirs(PROD_WEIGHTS, exist_ok=True)

    # 2. 同步 Poisson (Dixon-Coles) 专家逻辑
    poisson_src = os.path.join(RESEARCH_MODELS, "poisson.py")
    poisson_dst = os.path.join(PROD_CORE, "research_poisson.py")
    print(f"  - Deploying expert Poisson logic to {poisson_dst}")
    shutil.copy2(poisson_src, poisson_dst)

    # 3. 同步 Elo 专家逻辑
    elo_src = os.path.join(RESEARCH_MODELS, "elo.py")
    elo_dst = os.path.join(PROD_CORE, "research_elo.py")
    print(f"  - Deploying expert Elo logic to {elo_dst}")
    shutil.copy2(elo_src, elo_dst)

    # 4. 同步权重 JSON
    # Poisson 权重
    p_weights_src = "research/data/processed/poisson_expert_weights.json"
    p_weights_dst = os.path.join(PROD_WEIGHTS, "poisson_expert_weights.json")
    if os.path.exists(p_weights_src):
        print(f"  - Deploying expert Poisson weights to {p_weights_dst}")
        shutil.copy2(p_weights_src, p_weights_dst)

    # Elo 权重
    e_weights_src = "research/data/processed/elo_expert_weights.json"
    e_weights_dst = os.path.join(PROD_WEIGHTS, "elo_expert_weights.json")
    if os.path.exists(e_weights_src):
        print(f"  - Deploying expert Elo weights to {e_weights_dst}")
        shutil.copy2(e_weights_src, e_weights_dst)

    # 5. 同步已生成的静态内容
    STATIC_SRC = "research/reports"
    STATIC_DST = "static/research_gallery"
    if os.path.exists(STATIC_SRC):
        print(f"  - Syncing content gallery to {STATIC_DST}")
        if os.path.exists(STATIC_DST):
            shutil.rmtree(STATIC_DST)
        shutil.copytree(STATIC_SRC, STATIC_DST)

    print("\n✅ Sync Complete. The 'Race Car' is now equipped with the latest Lab components.")

if __name__ == "__main__":
    sync_breakthroughs()
