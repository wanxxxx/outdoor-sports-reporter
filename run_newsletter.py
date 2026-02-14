"""
通用新闻汇总任务执行模块

本模块提供通用的新闻汇总工作流：
1. run_newsletter_task: 运行完整的新闻汇总生成和发布任务（通用版本）
2. run_weekly_newsletter_task: 户外运动新闻汇总任务（向后兼容）
3. run_quick_test: 快速测试模式，使用预设数据验证流程

工作流程:
  fetch_articles → process_articles_with_ai → publish_feishu_report

使用示例:
    # 运行通用任务（需要提供配置）
    python run_newsletter.py
    
    # 运行户外运动新闻汇总（过去3天）
    python run_newsletter.py --days 3
    
    # 测试模式
    python run_newsletter.py --test
"""
import os
import argparse
from datetime import date, timedelta
from typing import Optional

# 直接从环境变量读取，不使用 dotenv
# load_dotenv()

from newsletter_tools import (
    NewsConfig,
    fetch_articles,
    fetch_outdoor_articles,
    process_articles_with_ai,
    publish_feishu_report
)


# 默认户外运动配置
def get_default_outdoor_config() -> NewsConfig:
    """
    获取默认的户外运动新闻配置
    
    Returns:
        NewsConfig: 户外运动新闻配置
    """
    from run_outdoor_news_summary import get_outdoor_ai_prompt, get_outdoor_ai_system_prompt
    
    target_sites = os.getenv('TARGET_SITES', '').split(',') if os.getenv('TARGET_SITES') else []
    target_sites = [site.strip() for site in target_sites if site.strip()]
    
    rss_feeds = {}
    rss_feeds_env = os.getenv('RSS_FEEDS', '')
    if rss_feeds_env:
        for mapping in rss_feeds_env.split(','):
            if '=' in mapping:
                site_url, rss_url = mapping.split('=', 1)
                rss_feeds[site_url.strip()] = rss_url.strip()
    
    feishu_openids = []
    openids_env = os.getenv('FEISHU_COLLABORATOR_OPENIDS', '')
    if openids_env:
        feishu_openids = [oid.strip() for oid in openids_env.split(',') if oid.strip()]
    
    return NewsConfig(
        name="outdoor_sports",
        target_sites=target_sites,
        rss_feeds=rss_feeds,
        ai_prompt=get_outdoor_ai_prompt(),
        ai_system_prompt=get_outdoor_ai_system_prompt(),
        feishu_collaborator_openids=feishu_openids,
        report_title_template="户外运动新闻汇总 ({start_date} 至 {end_date})",
        report_header="# 户外运动新闻汇总\n",
        cache_prefix="outdoor_"
    )


def run_newsletter_task(config: NewsConfig, 
                        chat_id: str = None,
                        days_back: int = None,
                        start_date: date = None,
                        end_date: date = None) -> Optional[str]:
    """
    通用新闻汇总任务执行函数
    
    支持两种模式：
    1. 按天数回溯：指定 days_back 参数，自动计算日期范围
    2. 指定日期范围：指定 start_date 和 end_date 参数
    
    Args:
        config: NewsConfig 配置对象
        chat_id: 飞书群组ID，为空则尝试从环境变量读取
        days_back: 回溯天数，与 start_date/end_date 互斥
        start_date: 开始日期，与 days_back 互斥
        end_date: 结束日期，与 days_back 互斥
    
    Returns:
        飞书文档链接，失败返回 None
    
    Raises:
        ValueError: 参数冲突时抛出
    """
    if days_back is not None and (start_date is not None or end_date is not None):
        raise ValueError("days_back 与 start_date/end_date 参数不能同时使用")
    
    if chat_id is None:
        chat_id = os.getenv('FEISHU_CHAT_ID')
    
    print("=" * 80)
    print(f"🚀 开始运行 {config.name} 新闻汇总生成任务")
    print("=" * 80)
    
    if days_back is not None:
        actual_end_date = date.today()
        actual_start_date = actual_end_date - timedelta(days=days_back)
        print(f"\n📅 文章日期范围: {actual_start_date} 至 {actual_end_date} (过去 {days_back} 天)")
    elif start_date is not None and end_date is not None:
        actual_start_date = start_date
        actual_end_date = end_date
        print(f"\n📅 文章日期范围: {actual_start_date} 至 {actual_end_date}")
    else:
        days_back = 7
        actual_end_date = date.today()
        actual_start_date = actual_end_date - timedelta(days=days_back)
        print(f"\n📅 文章日期范围: {actual_start_date} 至 {actual_end_date} (过去 {days_back} 天)")
    
    print("\n" + "=" * 80)
    print("🧹 清理过期缓存")
    print("=" * 80)
    
    from newsletter_tools import clean_all_expired_caches
    clean_all_expired_caches()
    
    print("\n" + "=" * 80)
    print(f"📥 步骤 1: 获取 {config.name} 相关文章")
    print("=" * 80)
    
    articles = fetch_articles(actual_start_date, actual_end_date, config=config)
    
    if not articles:
        print(f"\n⚠️ 在指定日期范围内未找到符合条件的 {config.name} 文章")
        return None
    
    print(f"\n✅ 共获取 {len(articles)} 篇文章")
    
    import json
    output_file = f'output/fetch_{config.name}_{actual_start_date}_to_{actual_end_date}.json'
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"📄 原始文章数据已保存到: {output_file}")
    
    print("\n" + "=" * 80)
    print("🤖 步骤 2: AI 处理文章内容")
    print("=" * 80)
    
    markdown_content = process_articles_with_ai(articles, config=config)
    
    if not markdown_content:
        print("\n❌ AI 处理失败，无法生成新闻汇总")
        return None
    
    md_output_file = f'output/ai_{config.name}_{actual_start_date}_to_{actual_end_date}.md'
    os.makedirs(os.path.dirname(md_output_file), exist_ok=True)
    with open(md_output_file, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"📄 Markdown 新闻汇总已保存到: {md_output_file}")
    
    article_count = markdown_content.count('\n## ')
    print(f"\n✅ AI 处理完成，共生成 {article_count} 篇文章的摘要")
    
    print("\n" + "=" * 80)
    print("📤 步骤 3: 发布到飞书")
    print("=" * 80)
    
    if chat_id:
        print(f"📨 将推送到群组: {chat_id}")
    else:
        print("⚠️ 未配置 FEISHU_CHAT_ID，将只创建文档，不发送消息")
    
    report_title = config.report_title_template.format(
        name=config.name,
        start_date=actual_start_date,
        end_date=actual_end_date
    )
    
    doc_url = publish_feishu_report(
        report_title, 
        markdown_content, 
        chat_id,
        collaborator_openids=config.feishu_collaborator_openids
    )
    
    if doc_url:
        print("\n" + "=" * 80)
        print("🎉 新闻汇总生成和发布任务完成！")
        print("=" * 80)
        print(f"\n📄 文档链接: {doc_url}")
        print(f"📅 涵盖日期: {actual_start_date} 至 {actual_end_date}")
        print(f"📝 文章数量: {article_count}")
    else:
        print("\n❌ 发布到飞书失败")
    
    return doc_url


def run_weekly_newsletter_task(chat_id: str = None, days_back: int = 7) -> Optional[str]:
    """
    运行户外运动新闻汇总生成任务（向后兼容函数）
    
    Args:
        chat_id: 飞书群组ID，为空则尝试从环境变量读取
        days_back: 回溯天数，默认7天
    
    Returns:
        飞书文档链接，失败返回 None
    """
    config = get_default_outdoor_config()
    return run_newsletter_task(config, chat_id=chat_id, days_back=days_back)


def run_quick_test(config: NewsConfig = None, chat_id: str = None) -> Optional[str]:
    """
    快速测试模式：使用预设的测试数据运行完整流程
    
    Args:
        config: NewsConfig 配置对象，为空则使用默认户外运动配置
        chat_id: 飞书群组ID，为空则尝试从环境变量读取
    """
    if chat_id is None:
        chat_id = os.getenv('FEISHU_CHAT_ID')
    
    # 使用默认配置
    if config is None:
        config = get_default_outdoor_config()
    
    print("=" * 80)
    print(f"🧪 运行快速测试模式 ({config.name})")
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
    markdown_content = process_articles_with_ai(articles, config=config)
    
    if not markdown_content:
        print("❌ AI 处理失败")
        return None
    
    md_file = f'test/output/ai_{config.name}_test_output.md'
    os.makedirs(os.path.dirname(md_file), exist_ok=True)
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"✅ Markdown 已保存到: {md_file}")
    
    report_title = f"{config.name}新闻汇总（测试）"
    
    if chat_id:
        print(f"\n📤 发布到飞书群组: {chat_id}")
        doc_url = publish_feishu_report(
            report_title, 
            markdown_content, 
            chat_id,
            collaborator_openids=config.feishu_collaborator_openids
        )
        return doc_url
    else:
        print("\n⚠️ 未配置 FEISHU_CHAT_ID，只创建文档不发消息")
        doc_url = publish_feishu_report(
            report_title, 
            markdown_content, 
            None,
            collaborator_openids=config.feishu_collaborator_openids
        )
        return doc_url


def main():
    parser = argparse.ArgumentParser(
        description='通用新闻汇总生成和发布工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 运行完整任务（默认户外运动，自动读取环境变量）
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
        # 使用默认配置运行测试
        run_quick_test()
    else:
        # 使用默认配置运行完整任务
        run_weekly_newsletter_task(days_back=args.days)


if __name__ == '__main__':
    main()
