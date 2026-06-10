import requests
import json
import os

class AIService:
    """
    对接 deepstock.zone.id 的 GPT-OSS-120B 模型。
    专门处理 reasoning 类型的响应。
    """
    def __init__(self):
        self.api_key = "sk-vpiG1geQ51q6w12NNOB92ktjJdZ6eDk3ysarFwmP5ztG3Vh6"
        self.base_url = "https://deepstock.zone.id/v1"
        self.model = "openai/gpt-oss-120b"

    def analyze_match(self, match_data: dict, lang: str = 'zh') -> str:
        """
        基于比赛数据生成战术前瞻。
        """
        prompt = self._build_prompt(match_data, lang)
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "你是一位拥有20年经验的足球战术评论员。你说话风格犀利、深刻，善于从赔率和xG数据中洞察真相。"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.5, # 降低随机性，减少废话
                    "max_tokens": 150
                },
                timeout=30 
            )
            res_json = response.json()
            
            if 'choices' in res_json and len(res_json['choices']) > 0:
                text = res_json['choices'][0]['message'].get('content', "").strip()
                
                # ─── 核心修复：输出清洗逻辑 ───
                import re
                # 1. 剔除常见的 AI 引言 (e.g. "好的", "当然", "这场比赛...")
                text = re.sub(r"^(好的|当然|作为.*?|根据数据|这里是|总结如下[:：])", "", text)
                # 2. 剔除任何包含指令的 meta-talk (如 We need to respond in Chinese)
                if "respond in" in text or "provide a" in text:
                    text = text.split('.')[-1].strip() # 尝试截取最后一句，或者丢弃
                
                # 3. 剔除引号
                text = text.replace('"', '').replace('“', '').replace('”', '')
                
                if len(text) > 5:
                    return text[:120] # 限制长度，保持精炼
            
            return "战术分析正在实时同步中..."
            
        except Exception as e:
            return "战术分析模块正在维护..."

    def _build_prompt(self, data: dict, lang: str) -> str:
        pairing = data['match_info']['pairing']
        h_xg = data['stats']['avg_xg']['home']
        a_xg = data['stats']['avg_xg']['away']
        h_win = data['prediction_ref']['home_win']
        a_win = data['prediction_ref']['away_win']

        # 💡 数据语境化处理：如果 xG 为 0，不告诉 AI 它是 0，而是说“样本重构中”
        h_desc = f"{h_xg:.2f}" if h_xg > 0 else "样本重构中"
        a_desc = f"{a_xg:.2f}" if a_xg > 0 else "战术重组中"
        
        if lang == 'zh':
            return (
                f"任务：对比赛【{pairing}】进行一句辛辣、极具战术深度的点评。\n"
                f"参考数据：主队xG({h_desc})，客队xG({a_desc})。模型预测主胜概率 {h_win:.1%}。\n"
                f"要求：\n"
                f"1. 禁止出现英语，禁止复述我的指令。\n"
                f"2. 直接给出结论，不要说‘好的’或‘这场比赛’等废话。\n"
                f"3. 风格要像顶级评论员（如詹俊或黄健翔），针对实力差距或xG趋势进行点评。\n"
                f"4. 字数控制在 40-60 字。"
            )
        else:
            return f"Match: {pairing}. Give a sharp, 30-word tactical verdict based on xG({h_desc} vs {a_desc}) and win prob {h_win:.1%}. Directly give the output."

if __name__ == "__main__":
    service = AIService()
    test_data = {
        "match_info": {"pairing": "葡萄牙 vs 哥伦比亚"},
        "stats": {"avg_xg": {"home": 1.62, "away": 1.35}},
        "prediction_ref": {"home_win": 0.45, "away_win": 0.27}
    }
    print("AI 最终集成实测:\n", service.analyze_match(test_data, 'zh'))
