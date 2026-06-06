import asyncio
from playwright.async_api import async_playwright
import os
import json

class ProRenderer:
    """
    高保真渲染引擎。
    使用 Playwright 捕获基于 React 模版的像素级无损截图。
    """
    def __init__(self, template_path: str):
        self.template_path = os.path.abspath(template_path)

    async def render(self, data: dict, output_path: str):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1200, 'height': 800})
            
            # 加载模板
            await page.goto(f"file://{self.template_path}")
            
            # 注入数据并调用渲染函数
            data_json = json.dumps(data)
            await page.evaluate(f"window.renderCard({data_json})")
            
            # 等待 webfont 和 react 渲染完成
            await page.wait_for_timeout(2000)
            
            # 截图目标容器
            element = await page.query_selector("#canvas")
            await element.screenshot(path=output_path, type='png', animations='disabled')
            
            await browser.close()
            print(f"✅ High-Fidelity Card Rendered: {output_path}")

if __name__ == "__main__":
    # 模拟数据测试
    test_data = {
        "activeSide": "home",
        "home": {
            "id": "POR", "name": "葡萄牙", "color": "#BF5AF2",
            "stats": {"overall": 89, "offense": 91, "defense": 78, "transition": 84},
            "heat_nodes": [{"x": "75%", "y": "25%", "size": "120px", "intensity": 0.6}, {"x": "85%", "y": "50%", "size": "150px", "intensity": 0.8}]
        },
        "away": {
            "id": "COL", "name": "哥伦比亚", "color": "#00D4FF",
            "stats": {"overall": 82, "offense": 75, "defense": 88, "transition": 91},
            "heat_nodes": []
        },
        "prediction": {"home": 45, "draw": 28, "away": 27},
        "ai_commentary": "葡萄牙利用边路内切压制对方三区，哥伦比亚需警惕快速攻防转换中的落位风险。"
    }
    
    renderer = ProRenderer("research/src/footy/evaluation/render_template.html")
    asyncio.run(renderer.render(test_data, "research/reports/cards/ai_integrated/PRO_FINAL_Portugal_vs_Colombia.png"))
