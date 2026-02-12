"""
户外运动周报任务执行模块

本模块提供两个核心任务函数：
1. run_weekly_newsletter_task: 运行完整的周报生成和发布任务
2. run_quick_test: 快速测试模式，使用预设数据验证流程

工作流程:
  fetch_outdoor_articles → process_articles_with_ai → publish_feishu_report

使用示例:
    # 运行完整任务（自动读取 FEISHU_CHAT_ID 环境变量）
    python run_newsletter.py
    
    # 运行完整任务（过去3天）
    python run_newsletter.py --days 3
    
    # 测试模式（自动读取 FEISHU_CHAT_ID 环境变量）
    python run_newsletter.py --test
"""
import os
import argparse
from datetime import date, timedelta
from typing import Optional

# 直接从环境变量读取，不使用 dotenv
# load_dotenv()

from newsletter_tools import (
    fetch_outdoor_articles,
    process_articles_with_ai,
    publish_feishu_report
)


def run_weekly_newsletter_task(chat_id: str = None, days_back: int = 7) -> Optional[str]:
    """
    运行完整的周报生成和发布任务
    
    Args:
        chat_id: 飞书群组ID，为空则尝试从环境变量读取
        days_back: 回溯天数，默认7天
    
    Returns:
        飞书文档链接，失败返回 None
    """
    if chat_id is None:
        chat_id = os.getenv('FEISHU_CHAT_ID')
    
    print("=" * 80)
    print("🚀 开始运行户外运动周报生成任务")
    print("=" * 80)
    
    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)
    
    print(f"\n📅 文章日期范围: {start_date} 至 {end_date} (过去 {days_back} 天)")
    
    print("\n" + "=" * 80)
    print("🧹 清理过期缓存")
    print("=" * 80)
    
    from newsletter_tools import clean_all_expired_caches
    clean_all_expired_caches()
    
    print("\n" + "=" * 80)
    print("📥 步骤 1: 获取户外运动相关文章")
    print("=" * 80)
    
    articles = fetch_outdoor_articles(start_date, end_date)
    
    if not articles:
        print("\n⚠️ 在指定日期范围内未找到符合条件的文章")
        return None
    
    print(f"\n✅ 共获取 {len(articles)} 篇文章")
    
    import json
    output_file = f'output/fetch_articles_{start_date}_to_{end_date}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"📄 原始文章数据已保存到: {output_file}")
    
    print("\n" + "=" * 80)
    print("🤖 步骤 2: AI 处理文章内容")
    print("=" * 80)
    
    markdown_content = process_articles_with_ai(articles)
    
    if not markdown_content:
        print("\n❌ AI 处理失败，无法生成周报")
        return None
    
    md_output_file = f'output/ai_{start_date}_to_{end_date}.md'
    with open(md_output_file, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"📄 Markdown 周报已保存到: {md_output_file}")
    
    article_count = markdown_content.count('\n## ')
    print(f"\n✅ AI 处理完成，共生成 {article_count} 篇文章的摘要")
    
    print("\n" + "=" * 80)
    print("📤 步骤 3: 发布到飞书")
    print("=" * 80)
    
    if chat_id:
        print(f"📨 将推送到群组: {chat_id}")
    else:
        print("⚠️ 未配置 FEISHU_CHAT_ID，将只创建文档，不发送消息")
    
    report_title = f"户外运动周报 ({start_date} 至 {end_date})"
    
    doc_url = publish_feishu_report(report_title, markdown_content, chat_id)
    
    if doc_url:
        print("\n" + "=" * 80)
        print("🎉 周报生成和发布任务完成！")
        print("=" * 80)
        print(f"\n📄 文档链接: {doc_url}")
        print(f"📅 涵盖日期: {start_date} 至 {end_date}")
        print(f"📝 文章数量: {article_count}")
    else:
        print("\n❌ 发布到飞书失败")
    
    return doc_url


def run_quick_test(chat_id: str = None) -> Optional[str]:
    """
    快速测试模式：使用预设的测试数据运行完整流程
    
    Args:
        chat_id: 飞书群组ID，为空则尝试从环境变量读取
    """
    if chat_id is None:
        chat_id = os.getenv('FEISHU_CHAT_ID')
    
    print("=" * 80)
    print("🧪 运行快速测试模式")
    print("=" * 80)
    
    import json
    
    test_file = 'test/data/test_ai_processing_data.json'
    
    if not os.path.exists(test_file):
        print(f"❌ 测试数据文件不存在: {test_file}")
        return None
    
    with open(test_file, 'r', encoding='utf-8') as f:
        articles = json.load(f)
    
    print(f"\n📄 加载测试数据: {len(articles)} 篇文章")
    
    print("\n🤖 开始 AI 处理...")
    markdown_content = process_articles_with_ai(articles)
    
    if not markdown_content:
        print("❌ AI 处理失败")
        return None
    
    md_file = 'test/output/ai_test_output.md'
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"✅ Markdown 已保存到: {md_file}")
    
    if chat_id:
        print(f"\n📤 发布到飞书群组: {chat_id}")
        report_title = "户外运动周报（测试）"
        doc_url = publish_feishu_report(report_title, markdown_content, chat_id)
        return doc_url
    else:
        print("\n⚠️ 未配置 FEISHU_CHAT_ID，只创建文档不发消息")
        report_title = "户外运动周报（测试）"
        doc_url = publish_feishu_report(report_title, markdown_content, None)
        return doc_url


def main():
    parser = argparse.ArgumentParser(
        description='户外运动周报生成和发布工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 运行完整任务（自动读取环境变量）
  python run_newsletter.py
  
  # 运行完整任务（过去3天）
  python run_newsletter.py --days 3
  
  # 测试模式
  python run_newsletter.py --test
        """
    )
    
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='回溯天数 (默认: 7)'
    )
    
    parser.add_argument(
        '--test',
        action='store_true',
        help='使用预设测试数据运行（不实时抓取）'
    )
    
    args = parser.parse_args()
    
    if args.test:
        run_quick_test()
    else:
        run_weekly_newsletter_task(days_back=args.days)


if __name__ == '__main__':
    main()
