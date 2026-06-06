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
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 500
                },
                timeout=60 
            )
            res_json = response.json()
            
            if 'choices' in res_json and len(res_json['choices']) > 0:
                msg = res_json['choices'][0]['message']
                # 兼容性处理：模型可能直接输出在 content，也可能输出在 reasoning 中
                content = msg.get('content')
                reasoning = msg.get('reasoning')
                
                final_text = content or reasoning or ""
                if final_text:
                    # 如果返回的是冗长的推理过程，截取前 100 字作为战术金句
                    return final_text[:200].strip() + "..."
            
            return "战术分析正在实时同步中 (Analysis Syncing...)"
            
        except Exception as e:
            print(f"⚠️ AI Analysis failed: {e}")
            return "战术分析模块正在维护 (Service Maintenance...)"

    def _build_prompt(self, data: dict, lang: str) -> str:
        pairing = data['match_info']['pairing']
        h_xg = data['stats']['avg_xg']['home']
        a_xg = data['stats']['avg_xg']['away']
        h_win = data['prediction_ref']['home_win']
        a_win = data['prediction_ref']['away_win']
        
        if lang == 'zh':
            return f"你是一位顶尖足球解说员。请对这场比赛进行一句话战术总结：{pairing}。数据：主队历史 xG {h_xg}，客队历史 xG {a_xg}。模型预测主胜概率 {h_win:.1%}，客胜概率 {a_win:.1%}。要求：字数 60 字内，辛辣、专业。"
        else:
            return f"Match: {pairing}. Data: Home xG {h_xg}, Away xG {a_xg}. Probabilities: Home {h_win:.1%}, Away {a_win:.1%}. Give a 40-word tactical verdict."

if __name__ == "__main__":
    service = AIService()
    test_data = {
        "match_info": {"pairing": "葡萄牙 vs 哥伦比亚"},
        "stats": {"avg_xg": {"home": 1.62, "away": 1.35}},
        "prediction_ref": {"home_win": 0.45, "away_win": 0.27}
    }
    print("AI 最终集成实测:\n", service.analyze_match(test_data, 'zh'))
