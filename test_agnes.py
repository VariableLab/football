
import requests
import json

def test_agnes_text():
    print("\n--- 测试文本生成 (agnes-2.0-flash) ---")
    url = "https://apihub.agnes-ai.com/v1/chat/completions"
    headers = {
        "Authorization": "Bearer sk-u0hbhlXSnFVIFWAx5xWe4om7OXtp9jakOpFyM96e0YmxYIjM",
        "Content-Type": "application/json"
    }
    data = {
        "model": "agnes-2.0-flash",
        "messages": [{"role": "user", "content": "你好，请确认你已准备好协助生成足球赛事内容。"}]
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

def test_agnes_image():
    print("\n--- 测试图片生成 (agnes-image-2.1-flash) ---")
    # 按照标准 OpenAI 格式尝试
    url = "https://apihub.agnes-ai.com/v1/images/generations"
    headers = {
        "Authorization": "Bearer sk-u0hbhlXSnFVIFWAx5xWe4om7OXtp9jakOpFyM96e0YmxYIjM",
        "Content-Type": "application/json"
    }
    data = {
        "model": "agnes-image-2.1-flash",
        "prompt": "A professional cinematic football match poster, Brazil vs France, ultra-realistic, 8k.",
        "n": 1,
        "size": "1024x1024"
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    text_ok = test_agnes_text()
    image_ok = test_agnes_image()
    
    if text_ok and image_ok:
        print("\n✅ Agnes AI API 密钥完全正常！")
    else:
        print("\n❌ 某些接口测试失败，请检查密钥权限或模型名称。")
