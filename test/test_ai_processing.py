import os
import sys

# 添加父目录到Python路径，以便导入newsletter_tools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. 强制移除代理环境变量 (在 import requests/openai 之前执行)
os.environ.pop(
"HTTP_PROXY", None
)
os.environ.pop(
"HTTPS_PROXY", None
)
os.environ.pop(
"ALL_PROXY", None
)

import json
from datetime import datetime
from dotenv import load_dotenv
from newsletter_tools import process_articles_with_ai

load_dotenv()

def clear_ai_cache():
    """清理AI缓存"""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache', 'ai')
    if os.path.exists(cache_dir):
        for filename in os.listdir(cache_dir):
            file_path = os.path.join(cache_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f"警告: 无法删除 {file_path}: {e}")
        print("✅ AI缓存已清理")
    else:
        print("⚠️ AI缓存目录不存在")

def main():
    print("=" * 80)
    print("AI 处理测试脚本")
    print("=" * 80)

    print("\n清理AI缓存...")
    clear_ai_cache()

    print("\n加载环境变量...")
    required_vars = ['LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL']
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"{var}: {value[:20]}...")
        else:
            print(f"{var}: 未设置")
            raise ValueError(f"环境变量 {var} 未设置，请检查 .env 文件")

    print("\n" + "-" * 80)
    print("读取测试数据...")
    test_file = os.path.join('test', 'data', 'test_ai_processing_data.json')

    with open(test_file, 'r', encoding='utf-8') as f:
        articles = json.load(f)
    print(f"成功读取 {len(articles)} 篇文章")

    print("\n" + "-" * 80)
    print("开始 AI 处理...")
    print("-" * 80)

    markdown_text = process_articles_with_ai(articles, batch_size=1)

    print("\n" + "=" * 80)
    print("AI 处理完成！")
    print("=" * 80)
    print(f"\n生成 {len(articles)} 篇文章的摘要")

    output_file = 'test/output/test_ai_processing_output.md'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(markdown_text)

    print(f"\nMarkdown 结果已保存到: {output_file}")

if __name__ == "__main__":
    main()
