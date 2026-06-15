import requests
import json
import os
import re

class AIService:
    """
    统一 AI 服务类，支持文本分析与高保真图像生成。
    """
    def __init__(self):
        # 文本模型配置 (保持原样)
        self.text_api_key = "sk-vpiG1geQ51q6w12NNOB92ktjJdZ6eDk3ysarFwmP5ztG3Vh6"
        self.text_base_url = "https://deepstock.zone.id/v1"
        self.text_model = "openai/gpt-oss-120b"
        
        # 视觉模型配置 (Agnes AI)
        self.vision_api_key = "sk-u0hbhlXSnFVIFWAx5xWe4om7OXtp9jakOpFyM96e0YmxYIjM"
        self.vision_base_url = "https://apihub.agnes-ai.com/v1"
        self.image_model = "agnes-image-2.1-flash"

        # 球队视觉画像映射表 (进一步细化南美/亚洲等特征，增加解剖学约束提示)
        self.PERSONA_MAP = {
            "Brazil": {
                "persona": "South American Afro-Brazilian heritage, olive-tanned skin, curly black hair",
                "jersey": "iconic yellow Brazil jersey with green collar, blue shorts, white socks",
                "style": "dynamic samba dribbling, explosive leg muscles, professional athlete physique"
            },
            "Germany": {
                "persona": "Central European (Germanic) features, tall athletic build, short blonde or brown hair",
                "jersey": "clean white German jersey with black and red accents, black shorts",
                "style": "precise posture, disciplined stride, powerful running form"
            },
            "Argentina": {
                "persona": "Argentine Latino features, light-tanned skin, intense eyes",
                "jersey": "sky blue and white vertical striped Argentina shirt, black shorts",
                "style": "low center of gravity, agile dribbling, focused intensity"
            },
            "France": {
                "persona": "Multicultural French squad, diverse athletic builds, mixed heritage",
                "jersey": "deep royal blue France jersey with red accents",
                "style": "explosive speed, elegant tactical power"
            },
            "Japan": {
                "persona": "East Asian (Japanese) features, agile and lean athletic build",
                "jersey": "Samurai blue Japan jersey with subtle origami patterns, white shorts",
                "style": "sharp turning movement, fast footwork, lean muscle definition"
            },
            "South Korea": {
                "persona": "East Asian (Korean) features, strong athletic posture",
                "jersey": "vibrant red Korea jersey with black trim",
                "style": "incredible stamina, explosive counter-attacks"
            },
            "Portugal": {
                "persona": "Southern European/Portuguese features, Mediterranean skin tone",
                "jersey": "deep red Portugal jersey with green trim",
                "style": "technical brilliance, sharp attacking focus"
            }
        }

    def analyze_match(self, match_data: dict, lang: str = 'zh') -> str:
        """生成战术前瞻文本"""
        pairing = match_data['match_info']['pairing']
        h_xg = match_data['stats']['avg_xg']['home']
        a_xg = match_data['stats']['avg_xg']['away']
        h_win = match_data['prediction_ref']['home_win']
        a_win = match_data['prediction_ref']['away_win']

        h_desc = f"{h_xg:.2f}" if h_xg > 0 else "样本重构中"
        a_desc = f"{a_xg:.2f}" if a_xg > 0 else "战术重组中"
        
        prompt = (
            f"任务：对比赛【{pairing}】进行一句辛辣、极具战术深度的点评。\n"
            f"参考数据：主队xG({h_desc})，客队xG({a_desc})。模型预测主胜概率 {h_win:.1%}。\n"
            f"要求：1. 直接给出结论，不要废话。2. 风格犀利。3. 字数 40-60 字。"
        )
        
        try:
            response = requests.post(
                f"{self.text_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.text_api_key}"},
                json={
                    "model": self.text_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                    "max_tokens": 150
                },
                timeout=30 
            )
            res_json = response.json()
            if 'choices' in res_json:
                text = res_json['choices'][0]['message'].get('content', "").strip()
                text = re.sub(r"^(好的|当然|根据数据|这里是|总结如下[:：])", "", text)
                return text[:120]
            return "战术分析正在实时同步中..."
        except:
            return "战术分析模块正在维护..."

    def generate_match_poster(self, home_name_en: str, away_name_en: str) -> str:
        """
        生成高保真赛事海报 URL。
        加入硬核解剖学约束 (Anatomical Constraints) 以减少三只脚等 BUG。
        """
        h_info = self.PERSONA_MAP.get(home_name_en, {"persona": "professional football player", "jersey": "team jersey", "style": "running"})
        a_info = self.PERSONA_MAP.get(away_name_en, {"persona": "professional football player", "jersey": "away jersey", "style": "running"})

        prompt = (
            f"A high-end cinematic 8k sports photography of a professional football match. "
            f"Full body shot of a {h_info['persona']} in {h_info['jersey']} and "
            f"a {a_info['persona']} in {a_info['jersey']}. "
            f"Composition: Two distinct players in action, side-by-side or facing each other with clear separation to avoid limb overlap. "
            f"Anatomy: Each player must have exactly TWO legs and TWO arms, perfectly rendered limbs, anatomically correct body proportions. "
            f"Action: {h_info['style']} vs {a_info['style']}. "
            f"Environment: Ultra-detailed grass texture, professional stadium background, dramatic cinematic sunset lighting, shallow depth of field. "
            f"Quality: Masterpiece, 8k resolution, no extra limbs, no fused bodies, no distortion, hyper-realistic skin and fabric textures."
        )

        try:
            url = f"{self.vision_base_url}/images/generations"
            headers = {
                "Authorization": f"Bearer {self.vision_api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.image_model,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024"
            }
            response = requests.post(url, headers=headers, json=data, timeout=60)
            res_json = response.json()
            
            if 'data' in res_json and len(res_json['data']) > 0:
                return res_json['data'][0]['url']
            return ""
        except Exception as e:
            print(f"Poster Generation Error: {e}")
            return ""

if __name__ == "__main__":
    service = AIService()
    # 测试生成一张 巴西 vs 德国 的海报
    print("正在生成加固后的海报提示词并调用 API...")
    poster_url = service.generate_match_poster("Brazil", "Germany")
    print(f"生成的加固版海报 URL: {poster_url}")
