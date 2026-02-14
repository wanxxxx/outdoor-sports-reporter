import os
import json
import re
import hashlib
import pickle
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import logging

import feedparser
import requests
from bs4 import BeautifulSoup
import trafilatura
from openai import OpenAI
from dotenv import load_dotenv

import lark_oapi as lark
from lark_oapi.api.docx.v1 import (
    CreateDocumentRequest,
    CreateDocumentRequestBody,
    ConvertDocumentRequest,
    ConvertDocumentRequestBody,
    TextElement,
    TextRun,
    TextElementStyle,
    Link,
    UpdateBlockRequest,
    BatchUpdateDocumentBlockRequest,
    BatchUpdateDocumentBlockRequestBody,
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
    Block,
    Text as TextModel
)
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.drive.v1 import (
    CreatePermissionMemberRequest,
    BaseMember,
    BatchCreatePermissionMemberRequest,
    BatchCreatePermissionMemberRequestBody
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class NewsConfig:
    """
    新闻汇总配置类
    
    用于封装不同类型新闻汇总的可配置参数，支持灵活扩展多种新闻类型。
    
    Attributes:
        name: 配置名称标识（如 "outdoor_sports", "tech_news"）
        target_sites: 目标网站URL列表
        rss_feeds: RSS源映射字典 {site_url: rss_url}
        ai_prompt: AI分析prompt模板
        ai_system_prompt: AI系统prompt
        feishu_collaborator_openids: 飞书协作者openid列表
        report_title_template: 新闻汇总标题模板，支持 {start_date} 和 {end_date} 占位符
        report_header: 新闻汇总标题（Markdown格式）
        cache_prefix: 缓存前缀，用于区分不同类型新闻的缓存
    """
    name: str
    target_sites: List[str] = field(default_factory=list)
    rss_feeds: Dict[str, str] = field(default_factory=dict)
    ai_prompt: str = ""
    ai_system_prompt: str = "你是一个专业的新闻分析助手，擅长批量提取文章关键信息并进行中英文翻译。"
    feishu_collaborator_openids: List[str] = field(default_factory=list)
    report_title_template: str = "{name}新闻汇总 ({start_date} 至 {end_date})"
    report_header: str = "# 新闻汇总\n"
    cache_prefix: str = ""

# 保存原始代理设置
_original_proxy_settings = {
    'HTTP_PROXY': os.environ.get('HTTP_PROXY'),
    'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'),
    'ALL_PROXY': os.environ.get('ALL_PROXY')
}

# RSS缓存配置
RSS_CACHE_DIR = "cache/rss"
RSS_CACHE_TTL = 3600  # 1小时缓存

# HTML抓取缓存配置
HTML_CACHE_DIR = "cache/html"
HTML_CACHE_TTL = 3600 * 6  # 6小时缓存

# AI处理缓存配置
AI_CACHE_DIR = "cache/ai"
AI_CACHE_TTL = 86400 * 7  # 7天缓存（AI处理结果长期有效）

# 创建缓存目录
os.makedirs(RSS_CACHE_DIR, exist_ok=True)
os.makedirs(HTML_CACHE_DIR, exist_ok=True)
os.makedirs(AI_CACHE_DIR, exist_ok=True)


def clean_expired_cache(cache_dir: str, ttl: int, cache_type: str = "cache"):
    """
    清理过期的缓存文件
    
    Args:
        cache_dir: 缓存目录路径
        ttl: 缓存有效期（秒）
        cache_type: 缓存类型（用于日志）
    """
    if not os.path.exists(cache_dir):
        return
    
    current_time = datetime.now().timestamp()
    cleaned_count = 0
    total_size = 0
    
    try:
        for filename in os.listdir(cache_dir):
            filepath = os.path.join(cache_dir, filename)
            
            if os.path.isfile(filepath):
                file_time = os.path.getmtime(filepath)
                file_age = current_time - file_time
                
                if file_age > ttl:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    cleaned_count += 1
                    total_size += file_size
                    logger.info(f"🗑️ 删除过期{cache_type}: {filename} (已过期 {file_age // 3600:.1f} 小时)")
        
        if cleaned_count > 0:
            size_mb = total_size / (1024 * 1024)
            logger.info(f"✅ {cache_type}清理完成: 删除 {cleaned_count} 个文件, 释放 {size_mb:.2f} MB")
        else:
            logger.info(f"✅ {cache_type}无需清理: 所有文件都在有效期内")
            
    except Exception as e:
        logger.warning(f"⚠️ 清理{cache_type}失败: {str(e)}")


def clean_all_expired_caches():
    """清理所有过期的缓存"""
    logger.info("🧹 开始清理过期缓存...")
    
    clean_expired_cache(RSS_CACHE_DIR, RSS_CACHE_TTL, "RSS缓存")
    clean_expired_cache(HTML_CACHE_DIR, HTML_CACHE_TTL, "HTML缓存")
    clean_expired_cache(AI_CACHE_DIR, AI_CACHE_TTL, "AI缓存")
    
    logger.info("🧹 缓存清理完成")


def get_rss_cache_path(rss_url: str) -> str:
    """获取RSS缓存文件路径"""
    # 使用URL的hash作为文件名
    url_hash = hashlib.md5(rss_url.encode()).hexdigest()
    return os.path.join(RSS_CACHE_DIR, f"{url_hash}.pkl")


def load_rss_from_cache(rss_url: str) -> Optional[feedparser.FeedParserDict]:
    """从缓存加载RSS数据"""
    cache_path = get_rss_cache_path(rss_url)
    
    if not os.path.exists(cache_path):
        return None
    
    try:
        # 检查缓存文件是否过期
        cache_time = os.path.getmtime(cache_path)
        current_time = datetime.now().timestamp()
        
        if current_time - cache_time > RSS_CACHE_TTL:
            logger.info(f"📦 RSS缓存过期: {rss_url}")
            return None
        
        with open(cache_path, 'rb') as f:
            cached_data = pickle.load(f)
        logger.info(f"📦 RSS缓存命中: {rss_url}")
        return cached_data
        
    except Exception as e:
        logger.warning(f"📦 RSS缓存加载失败: {rss_url} - {str(e)}")
        return None


def save_rss_to_cache(rss_url: str, feed_data: feedparser.FeedParserDict) -> bool:
    """保存RSS数据到缓存"""
    cache_path = get_rss_cache_path(rss_url)
    
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(feed_data, f)
        logger.info(f"📦 RSS缓存保存: {rss_url}")
        return True
    except Exception as e:
        logger.warning(f"📦 RSS缓存保存失败: {rss_url} - {str(e)}")
        return False


# ================================
# HTML抓取缓存函数
# ================================

def get_html_cache_path(url: str) -> str:
    """获取HTML缓存文件路径"""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(HTML_CACHE_DIR, f"{url_hash}.json")


def load_html_from_cache(url: str) -> Optional[str]:
    """从缓存加载HTML内容"""
    cache_path = get_html_cache_path(url)
    
    if not os.path.exists(cache_path):
        return None
    
    try:
        cache_time = os.path.getmtime(cache_path)
        current_time = datetime.now().timestamp()
        
        if current_time - cache_time > HTML_CACHE_TTL:
            logger.info(f"📦 HTML缓存过期: {url}")
            return None
        
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached_data = json.load(f)
        logger.info(f"📦 HTML缓存命中: {url}")
        return cached_data.get('content')
        
    except Exception as e:
        logger.warning(f"📦 HTML缓存加载失败: {url} - {str(e)}")
        return None


def save_html_to_cache(url: str, content: str) -> bool:
    """保存HTML内容到缓存"""
    cache_path = get_html_cache_path(url)
    
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({'content': content, 'timestamp': datetime.now().isoformat()}, f, ensure_ascii=False)
        # logger.info(f"📦 HTML缓存保存: {url}")
        return True
    except Exception as e:
        logger.warning(f"📦 HTML缓存保存失败: {url} - {str(e)}")
        return False


# ================================
# AI处理缓存函数
# ================================

def get_ai_cache_path(url: str) -> str:
    """获取AI缓存文件路径"""
    # 使用URL的hash作为文件名
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(AI_CACHE_DIR, f"{url_hash}.json")


def load_ai_from_cache(url: str) -> Optional[Dict]:
    """从缓存加载AI处理结果"""
    cache_path = get_ai_cache_path(url)
    
    if not os.path.exists(cache_path):
        return None
    
    try:
        # 检查缓存文件是否过期
        cache_time = os.path.getmtime(cache_path)
        current_time = datetime.now().timestamp()
        
        if current_time - cache_time > AI_CACHE_TTL:
            logger.info(f"📦 AI缓存过期: {url}")
            return None
        
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached_data = json.load(f)
        # logger.info(f"📦 AI缓存命中: {url}")
        return cached_data
        
    except Exception as e:
        logger.warning(f"📦 AI缓存加载失败: {url} - {str(e)}")
        return None


def save_ai_to_cache(url: str, ai_result: Dict) -> bool:
    """保存AI处理结果到缓存"""
    cache_path = get_ai_cache_path(url)
    
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(ai_result, f, ensure_ascii=False, indent=2)
        logger.info(f"📦 AI缓存保存: {url}")
        return True
    except Exception as e:
        logger.warning(f"📦 AI缓存保存失败: {url} - {str(e)}")
        return False


def parse_rss_with_cache(rss_url: str) -> Optional[feedparser.FeedParserDict]:
    """解析RSS，支持缓存"""
    # 尝试从缓存加载
    feed = load_rss_from_cache(rss_url)
    if feed:
        return feed
    
    # 缓存未命中，解析RSS
    logger.info(f"🔍 解析RSS: {rss_url}")
    feed = feedparser.parse(rss_url)
    
    if feed.entries:
        # 保存到缓存
        save_rss_to_cache(rss_url, feed)
    
    return feed


def enable_proxy_for_web_scraping():
    """
    恢复代理设置（用于网站抓取）
    """
    # 恢复代理环境变量
    for key, value in _original_proxy_settings.items():
        if value:
            os.environ[key] = value
    logger.info("🌐 恢复代理设置 (用于网站抓取)")

def clear_all_proxy():
    """
    完全清除所有代理设置
    """
    # 清除所有可能的代理变量
    proxy_vars = [
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy',
        'HTTP_PROXY_HOST', 'HTTP_PROXY_PORT',
        'HTTPS_PROXY_HOST', 'HTTPS_PROXY_PORT',
        'NO_PROXY', 'no_proxy'
    ]
    for var in proxy_vars:
        os.environ.pop(var, None)
    
    # 禁用当前进程的所有网络代理
    os.environ['NO_PROXY'] = '*'
    os.environ['no_proxy'] = '*'
    
    # 清除requests的代理设置
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    session = requests.Session()
    session.trust_env = False
    retry_strategy = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    logger.info("🚫 完全清除所有代理设置")


def get_feishu_client():
    """
    获取飞书客户端（自动清除代理）
    """
    clear_all_proxy()
    
    try:
        client = lark.Client.builder() \
            .app_id(os.getenv("FEISHU_APP_ID")) \
            .app_secret(os.getenv("FEISHU_APP_SECRET")) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        
        return client
    except Exception as e:
        logger.error(f"创建飞书客户端失败: {e}")
        # 如果配置失败，创建最基本的客户端
        client = lark.Client.builder() \
            .app_id(os.getenv("FEISHU_APP_ID")) \
            .app_secret(os.getenv("FEISHU_APP_SECRET")) \
            .build()
        return client


def _get_openai_client():
    """
    获取OpenAI客户端（清除代理）
    """
    clear_all_proxy()
    
    api_key = os.getenv('LLM_API_KEY')
    base_url = os.getenv('LLM_BASE_URL')
    
    if not api_key:
        raise ValueError('LLM_API_KEY environment variable is not set')
    
    client_kwargs = {'api_key': api_key}
    if base_url:
        client_kwargs['base_url'] = base_url
    
    return OpenAI(**client_kwargs)

# 从环境变量读取网站列表，格式：用逗号分隔的URL列表
TARGET_SITES = os.getenv('TARGET_SITES', '').split(',') if os.getenv('TARGET_SITES') else []
TARGET_SITES = [site.strip() for site in TARGET_SITES if site.strip()]

# 从环境变量读取RSS映射，格式：site1_url=rss1_url,site2_url=rss2_url
RSS_FEEDS_ENV = os.getenv('RSS_FEEDS', '')
RSS_FEEDS = {}

if RSS_FEEDS_ENV:
    for mapping in RSS_FEEDS_ENV.split(','):
        if '=' in mapping:
            site_url, rss_url = mapping.split('=', 1)
            RSS_FEEDS[site_url.strip()] = rss_url.strip()

def fetch_outdoor_articles(start_date: date, end_date: date, max_workers: int = 3) -> List[Dict]:
    """
    并行抓取户外运动相关文章（向后兼容函数）
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        max_workers: 最大并行工作线程数（仅用于网站级并发）
    
    Returns:
        文章列表
    """
    return fetch_articles(start_date, end_date, max_workers=max_workers)


def fetch_articles(start_date: date, end_date: date, 
                   config: NewsConfig = None,
                   target_sites: List[str] = None,
                   rss_feeds: Dict[str, str] = None,
                   max_workers: int = 3) -> List[Dict]:
    """
    并行抓取新闻文章（通用版本）
    
    支持两种调用方式：
    1. 通过 NewsConfig 配置对象传入参数
    2. 直接传入 target_sites 和 rss_feeds 参数
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        config: NewsConfig 配置对象（优先使用）
        target_sites: 目标网站列表（config 为空时使用）
        rss_feeds: RSS源映射（config 为空时使用）
        max_workers: 最大并行工作线程数（仅用于网站级并发）
    
    Returns:
        文章列表
    """
    sites = config.target_sites if config else (target_sites or TARGET_SITES)
    feeds = config.rss_feeds if config else (rss_feeds or RSS_FEEDS)
    
    logger.info(f"🚀 开始并行抓取文章: {start_date} 到 {end_date}")
    
    enable_proxy_for_web_scraping()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        site_url_map = {}
        for site_url in sites:
            rss_feed = feeds.get(site_url)
            
            if rss_feed:
                future = executor.submit(_fetch_from_rss, rss_feed, site_url, start_date, end_date)
            else:
                future = executor.submit(_fetch_from_html, site_url, start_date, end_date)
            
            futures.append(future)
            site_url_map[id(future)] = site_url
        
        articles = []
        completed = 0
        for future in as_completed(futures):
            try:
                site_result = future.result()
                current_site_url = site_url_map.get(id(future), "未知网站")
                
                if isinstance(site_result, dict):
                    site_articles = site_result.get('articles', [])
                    articles.extend(site_articles)
                elif isinstance(site_result, list):
                    articles.extend(site_result)
                else:
                    logger.warning(f"⚠️ 未知返回类型: {type(site_result)}")
                
                completed += 1
                logger.info(f"✅ 完成 {completed}/{len(sites)} 个网站：{current_site_url}")
            except Exception as e:
                current_site_url = site_url_map.get(id(future), "未知网站")
                logger.error(f"❌ 抓取网站失败: {current_site_url} - {str(e)}")
                completed += 1
    
    logger.info(f"🎉 所有网站抓取完成，共获取 {len(articles)} 篇文章")
    return articles


def _clean_rss_content(raw_html: str) -> str:
    # 1. 处理转义字符：将 \\n 替换为换行，将 \\\" 替换为引号
    content = raw_html.replace('\\n', '\n').replace('\\"', '"')
    
    # 2. 使用 BeautifulSoup 解析 HTML
    soup = BeautifulSoup(content, 'html.parser')
    
    # 3. 移除不相关的标签（如图片说明、脚本、样式）
    for extra in soup(['figure', 'script', 'style', 'img']):
        extra.decompose()
        
    # 4. 获取文本，并处理掉 RSS 中常见的重复链接（比如 "The post...appeared first on..."）
    lines = []
    for p in soup.find_all(['p', 'h1', 'h2', 'h3']):
        text = p.get_text().strip()
        # 过滤掉 RSS 自动生成的末尾推广语
        if "The post" in text and "appeared first on" in text:
            continue
        if text:
            lines.append(text)
    
    # 5. 去重（RSS 有时会重复推送正文片段）
    unique_lines = []
    for line in lines:
        if line not in unique_lines:
            unique_lines.append(line)
            
    return "\n\n".join(unique_lines)


def _fetch_from_rss(rss_url: str, site_url: str, start_date: date, end_date: date) -> List[Dict]:
    """
    从RSS源抓取文章（串行执行，简化逻辑）
    """
    articles = []
    
    try:
        # 使用缓存解析RSS
        feed = parse_rss_with_cache(rss_url)
        if not feed:
            logger.warning(f"RSS解析失败: {rss_url}")
            return articles
            
        logger.info(f"🔍 RSS[{rss_url}] 中共有 {len(feed.entries)} 条目")
        
        # 步骤1: 解析RSS（快速，本地处理）
        # 步骤2: 过滤日期范围并直接提取RSS内容（避免网页抓取）
        article_data = []
        
        for entry in feed.entries:
            if hasattr(entry, 'published_parsed'):
                article_date = datetime(*entry.published_parsed[:6])
                title = entry.get('title', '')
                
                if start_date <= article_date.date() <= end_date:
                    # 文章日期在范围内，直接从RSS提取内容
                    article_url = entry.get('link', '')
                    
                    # 直接从RSS条目中提取内容
                    description = entry.get('description', '')
                    summary = entry.get('summary', '')
                    
                    # 尝试获取完整的文章内容
                    content_encoded = ''
                    if entry.get('content'):
                        # feedparser会将content字段解析为列表
                        content_list = entry.get('content', [])
                        if content_list and len(content_list) > 0:
                            content_encoded = content_list[0].get('value', '')
                    
                    # 构建完整文章数据
                    article_data.append({
                        'title': title,
                        'url': article_url,
                        'date': article_date.date().isoformat(),
                        'site': site_url,
                        'description': description,
                        'summary': summary,
                        'content_encoded': content_encoded,
                        'raw_content': description + ' ' + summary + ' ' + content_encoded
                    })
        
        logger.info(f"📅 RSS[{rss_url}] 找到 {len(article_data)} 篇符合日期的文章")
        
        # 步骤3: 直接使用RSS内容，避免网页抓取
        if article_data:
            for data in article_data:
                try:
                    # 清理RSS内容，移除HTML标签和元数据
                    content_text = _clean_rss_content(data['raw_content'])
                    
                    if content_text and len(content_text) > 50:  # 确保有足够的内容
                        articles.append({
                            'site': data['site'],
                            'url': data['url'],
                            'title': data['title'],
                            'date': data['date'],
                            'content_text': content_text
                        })
                    else:
                        logger.warning(f"RSS内容质量较差: {data['url']} (内容长度: {len(content_text)})")
                        
                except Exception as e:
                    logger.warning(f"处理RSS内容失败: {data['url']} - {str(e)}")
        
    except Exception as e:
        logger.error(f"RSS抓取失败 {rss_url}: {str(e)}")
    
    return articles


def _fetch_from_html(site_url: str, start_date: date, end_date: date) -> Dict:
    """
    从HTML页面抓取文章（改进错误处理，保留所有有价值数据）
    返回：{
        'articles': List[Dict],  # 成功处理的文章
        'failed_articles': List[Dict],  # 处理失败的文章（保留基本信息）
        'statistics': Dict  # 详细统计信息
    }
    """
    result = {
        'articles': [],
        'failed_articles': [],
        'statistics': {
            'total_entries': 0,
            'filtered_by_date': 0,
            'successful_extraction': 0,
            'failed_extraction': 0,
            'error_messages': []
        }
    }
    
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import ssl
    import urllib3
    
    session = requests.Session()
    
    original_env_backup = os.environ.copy()
    
    BROWSER_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"'
    }
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    try:
        enable_proxy_for_web_scraping()
        session.trust_env = True
        session.headers.update(BROWSER_HEADERS)
        
        logger.info(f"🌐 尝试通过代理访问: {site_url}")
        response = session.get(site_url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        article_links = _extract_article_links(soup, site_url)
        result['statistics']['total_entries'] = len(article_links)
        
        for link in article_links:
            try:
                content_text = _extract_content_with_session(link, session)
                
                if content_text:
                    result['articles'].append({
                        'site': site_url,
                        'url': link,
                        'title': _extract_title_from_url(link),
                        'content_text': content_text,
                        'date': None
                    })
                    result['statistics']['successful_extraction'] += 1
                else:
                    result['failed_articles'].append({
                        'site': site_url,
                        'url': link,
                        'title': _extract_title_from_url(link),
                        'error': '内容提取失败',
                        'date': None
                    })
                    result['statistics']['failed_extraction'] += 1
                    
            except Exception as e:
                error_msg = f"处理链接失败: {link} - {str(e)}"
                logger.warning(f"⚠️ {error_msg}")
                result['statistics']['error_messages'].append(error_msg)
                
                result['failed_articles'].append({
                    'site': site_url,
                    'url': link,
                    'title': _extract_title_from_url(link),
                    'error': str(e),
                    'date': None
                })
                result['statistics']['failed_extraction'] += 1
        
    except Exception as proxy_error:
        proxy_error_msg = str(proxy_error)
        logger.warning(f"⚠️ 代理访问失败: {proxy_error_msg}")
        
        if 'ProxyError' in proxy_error_msg or 'SSL' in proxy_error_msg or 'proxy' in proxy_error_msg.lower():
            logger.info(f"🔄 尝试直连访问（绕过代理）: {site_url}")
            
            try:
                session.close()
                session = requests.Session()
                
                clear_all_proxy()
                session.trust_env = False
                session.headers.update(BROWSER_HEADERS)
                
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                
                response = session.get(site_url, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                
                article_links = _extract_article_links(soup, site_url)
                result['statistics']['total_entries'] = len(article_links)
                
                for link in article_links:
                    try:
                        content_text = _extract_content_with_session_direct(link, session)
                        
                        if content_text:
                            result['articles'].append({
                                'site': site_url,
                                'url': link,
                                'title': _extract_title_from_url(link),
                                'content_text': content_text,
                                'date': None
                            })
                            result['statistics']['successful_extraction'] += 1
                        else:
                            result['failed_articles'].append({
                                'site': site_url,
                                'url': link,
                                'title': _extract_title_from_url(link),
                                'error': '内容提取失败',
                                'date': None
                            })
                            result['statistics']['failed_extraction'] += 1
                            
                    except Exception as e:
                        error_msg = f"处理链接失败: {link} - {str(e)}"
                        logger.warning(f"⚠️ {error_msg}")
                        result['statistics']['error_messages'].append(error_msg)
                        
                        result['failed_articles'].append({
                            'site': site_url,
                            'url': link,
                            'title': _extract_title_from_url(link),
                            'error': str(e),
                            'date': None
                        })
                        result['statistics']['failed_extraction'] += 1
                
                logger.info(f"✅ 直连访问成功: {site_url}")
                
            except Exception as direct_error:
                error_msg = f"HTML抓取失败（代理和直连均失败） {site_url}: 代理错误={proxy_error_msg}, 直连错误={str(direct_error)}"
                logger.error(f"❌ {error_msg}")
                result['statistics']['error_messages'].append(error_msg)
        else:
            error_msg = f"HTML抓取失败 {site_url}: {proxy_error_msg}"
            logger.error(f"❌ {error_msg}")
            result['statistics']['error_messages'].append(error_msg)
    
    finally:
        session.close()
        os.environ.clear()
        os.environ.update(original_env_backup)
    
    return result


def _extract_content_with_session_direct(url: str, session: requests.Session) -> Optional[str]:
    """直连方式提取内容（不使用代理）"""
    cached_content = load_html_from_cache(url)
    if cached_content:
        return cached_content
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        response = session.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.content, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.decompose()
            
            content = soup.get_text()
            if content and len(content.strip()) > 100:
                content = content.strip()
                save_html_to_cache(url, content)
                return content
                
    except Exception as e:
        logger.warning(f"直连内容提取失败: {url} - {str(e)}")
    
    return None


def _extract_content_with_session(url: str, session: requests.Session) -> Optional[str]:
    """使用指定会话提取内容，用于支持VPN代理"""
    cached_content = load_html_from_cache(url)
    if cached_content:
        return cached_content
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        
        response = session.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.content, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.decompose()
            
            content = soup.get_text()
            if content and len(content.strip()) > 100:
                content = content.strip()
                save_html_to_cache(url, content)
                return content
                
    except Exception as e:
        logger.warning(f"后备内容提取失败: {url} - {str(e)}")
    
    return None


def _extract_article_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    links = []
    
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        
        if href.startswith('/'):
            href = base_url.rstrip('/') + href
        elif not href.startswith('http'):
            continue
        
        if _is_article_link(href):
            links.append(href)
    
    return list(set(links))


def _is_article_link(url: str) -> bool:
    exclude_patterns = ['#', '/tag/', '/category/', '/author/', '/page/', 'login', 'register']
    
    for pattern in exclude_patterns:
        if pattern in url:
            return False
    
    return True


def _extract_content(url: str) -> Optional[str]:
    """
    提取文章内容（简化为后备方案）
    注意：现在RSS已提供完整内容，此函数仅在RSS内容质量极差时使用
    """
    try:
        # 获取配置的会话
        session = globals().get('_scraping_session', None)
        
        # 简化请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        if session:
            response = session.get(url, headers=headers, timeout=15)
        else:
            import requests
            response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # 移除脚本和样式
            for script in soup(["script", "style"]):
                script.decompose()
            
            # 提取文本
            content = soup.get_text()
            if content and len(content.strip()) > 100:
                return content.strip()
                
    except Exception as e:
        logger.warning(f"后备内容提取失败: {url} - {str(e)}")
    
    return None


def _extract_title_from_url(url: str) -> str:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        title_tag = soup.find('title')
        if title_tag:
            return title_tag.get_text().strip()
        
        h1_tag = soup.find('h1')
        if h1_tag:
            return h1_tag.get_text().strip()
    except Exception as e:
        pass
    
    return url


def _is_english(text: str) -> bool:
    if not text:
        return False
    
    english_chars = sum(1 for char in text if char.isalpha() and ord(char) < 128)
    total_chars = sum(1 for char in text if char.isalpha())
    
    if total_chars == 0:
        return False
    
    return english_chars / total_chars > 0.5



def process_articles_with_ai(articles_list: List[Dict], 
                              config: NewsConfig = None,
                              max_workers: int = 10, 
                              batch_size: int = 3) -> str:
    """
    批量并行处理文章并生成Markdown（支持缓存和自定义配置）
    
    Args:
        articles_list: 文章列表
        config: NewsConfig 配置对象（包含 AI prompt 等配置）
        max_workers: 最大并行工作线程数
        batch_size: 每个批量处理的文章数量（建议3-5篇）
    
    Returns:
        Markdown格式的新闻汇总文本
    """
    if not articles_list:
        return ''
    
    cached_articles = []
    articles_to_process = []
    
    for article in articles_list:
        url = article.get('url', '')
        cached_result = load_ai_from_cache(url)
        if cached_result:
            # logger.info(f"🚀 AI缓存命中: {url}")
            cached_articles.append(cached_result)
        else:
            articles_to_process.append(article)
    
    logger.info(f"📊 AI缓存统计: {len(cached_articles)}篇命中缓存, {len(articles_to_process)}篇需要AI处理")
    
    if not articles_to_process:
        logger.info("✅ 所有文章均命中缓存，跳过AI处理")
        return _generate_markdown(cached_articles, config)
    
    try:
        client = _get_openai_client()
    except Exception as e:
        logger.error(f"初始化AI客户端失败: {str(e)}")
        if cached_articles:
            return _generate_markdown(cached_articles, config)
        return ''
    
    logger.info(f"🚀 开始批量并行AI处理: {len(articles_to_process)} 篇文章")
    logger.info(f"🤖 AI批量设置: batch_size={batch_size}, max_workers={max_workers}")
    
    batches = [articles_to_process[i:i + batch_size] for i in range(0, len(articles_to_process), batch_size)]
    logger.info(f"🤖 分为 {len(batches)} 个批次进行并行处理")
    
    newly_processed_articles = []
    completed = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        batch_index_map = {}
        for i, batch in enumerate(batches, 1):
            future = executor.submit(_process_batch_with_ai, client, batch, i, config)
            futures.append(future)
            batch_index_map[id(future)] = i
        
        for future in as_completed(futures):
            try:
                batch_results = future.result()
                newly_processed_articles.extend(batch_results)
                completed += 1
                current_batch_index = batch_index_map.get(id(future), completed)
                logger.info(f"✅ 完成 {completed}/{len(batches)} 个批次 (批次 {current_batch_index})")
            except Exception as e:
                current_batch_index = batch_index_map.get(id(future), "未知")
                logger.error(f"❌ 批量处理失败: 批次 {current_batch_index} - {str(e)}")
                completed += 1
    
    all_processed_articles = cached_articles + newly_processed_articles
    logger.info(f"🎉 批量并行AI处理完成: {len(cached_articles)}篇来自缓存, {len(newly_processed_articles)}篇新处理, 总计{len(all_processed_articles)}篇")
    
    markdown_text = _generate_markdown(all_processed_articles, config)
    
    return markdown_text


def _process_batch_with_ai(client: OpenAI, batch: List[Dict], batch_index: int, 
                           config: NewsConfig = None) -> List[Dict]:
    """
    批量处理文章（一次处理多篇文章，支持缓存和自定义配置）
    
    Args:
        client: OpenAI客户端
        batch: 文章批次
        batch_index: 批次索引
        config: NewsConfig 配置对象（包含自定义 AI prompt）
    
    Returns:
        处理后的文章列表
    """
    if not batch:
        return []
    
    logger.info(f"🔄 处理批次 {batch_index}: {len(batch)} 篇文章")
    
    cached_results = []
    articles_to_process = []
    
    for article in batch:
        url = article.get('url', '')
        cached_result = load_ai_from_cache(url)
        if cached_result:
            logger.info(f"🚀 AI缓存命中: {url}")
            cached_results.append((article, cached_result))
        else:
            articles_to_process.append(article)
    
    if not articles_to_process:
        logger.info(f"✅ 批次 {batch_index} 全部命中缓存")
        return [result for _, result in cached_results]
    
    if len(articles_to_process) < len(batch):
        logger.info(f"📦 批次 {batch_index}: {len(cached_results)}篇命中缓存, {len(articles_to_process)}篇需要AI处理")
    
    articles_info = []
    for i, article in enumerate(articles_to_process, 1):
        title = article.get('title', '')
        content_text = article.get('content_text', '')
        url = article.get('url', '')
        date_str = article.get('date', '')
        
        articles_info.append(f"""
文章 {i}:
标题: {title}
链接: {url}
日期: {date_str}
正文: {content_text[:1500]}...
""")
    
    batch_content = '\n'.join(articles_info)
    
    if not config or not config.ai_prompt:
        raise ValueError("NewsConfig.ai_prompt is required for AI processing")
    
    if not config.ai_system_prompt:
        raise ValueError("NewsConfig.ai_system_prompt is required for AI processing")
    
    ai_prompt_template = config.ai_prompt
    ai_system_prompt = config.ai_system_prompt
    
    prompt = ai_prompt_template.format(
        article_count=len(articles_to_process),
        batch_content=batch_content
    )
    
    try:
        model_name = os.getenv('LLM_MODEL')
        if not model_name:
            raise ValueError('LLM_MODEL environment variable is not set')

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {'role': 'system', 'content': ai_system_prompt},
                {'role': 'user', 'content': prompt}
            ],
            temperature=0.3,
            response_format={'type': 'json_object'},
            timeout=60
        )
        
        result_text = response.choices[0].message.content.strip()
        
        logger.info(f"🤖 AI返回原始内容长度: {len(result_text)} 字符")
        logger.info(f"🤖 AI返回原始内容: {result_text}")
        
        # 清理 Markdown 代码块标记（AI 有时会返回 ```json ... ```）
        if result_text.startswith('```'):
            lines = result_text.split('\n')
            result_text = '\n'.join(lines[1:-1]) if len(lines) > 2 else result_text
            result_text = result_text.strip()
        
        # 解析JSON结果
        import json
        results = json.loads(result_text)
        
        logger.info(f"🔍 解析后的results类型: {type(results)}")
        logger.info(f"🔍 解析后的results内容: {str(results)[:1000]}...")
        
        # 确保结果是数组格式
        if not isinstance(results, list):
            logger.warning(f"批次 {batch_index} 返回格式异常，尝试修复...")
            results = [results] if not isinstance(results, list) else results
        
        logger.info(f"🔍 得到 {len(results)} 篇文章")
        
        # 将结果映射回原始文章数据
        # 处理AI返回的结果
        newly_processed = []
        for i, result in enumerate(results):
            if i < len(articles_to_process):  # 确保不超过需要处理的文章数量
                article = articles_to_process[i]
                
                # 确保result是字典格式
                if not isinstance(result, dict):
                    logger.warning(f"批次 {batch_index} 文章 {i+1} 结果格式异常: {type(result)}")
                    result = {'chinese_title': article.get('title', '')}
                
                processed_article = {
                    'original_title': article.get('title', ''),
                    'chinese_title': result.get('chinese_title', article.get('title', '')),
                    'summary': result.get('summary', article.get('content_text', '')[:200] + '...'),
                    'key_persons': result.get('key_persons', []),
                    'key_person_bios': result.get('key_person_bios', []),
                    'location_name': result.get('location_name', '未知地点'),
                    'location_context': result.get('location_context', ''),
                    'event_date': result.get('event_date', article.get('date', '')),
                    'curated_angles': result.get('curated_angles', []),
                    'url': article.get('url', ''),
                    'date': article.get('date', ''),
                    'site': article.get('site', ''),
                    'ai_processed_at': datetime.now().isoformat(),
                    'content_length': len(article.get('content_text', ''))
                }
                newly_processed.append(processed_article)
        
        # 合并缓存结果和新处理的结果
        all_processed = [result for _, result in cached_results] + newly_processed
        
        logger.info(f"✅ 批次 {batch_index} 处理成功: {len(cached_results)}篇来自缓存, {len(newly_processed)}篇新处理")
        
        # 只为新处理的文章保存AI缓存
        for processed_article in newly_processed:
            url = processed_article.get('url', '')
            if url:
                save_ai_to_cache(url, processed_article)
        
        return all_processed
        
    except Exception as e:
        logger.error(f"批次 {batch_index} AI处理失败: {str(e)}")
        
        # 失败时返回原始数据
        processed = []
        for article in batch:
            processed.append({
                'original_title': article.get('title', ''),
                'chinese_title': article.get('title', ''),
                'summary': article.get('content_text', '')[:200] + '...',
                'key_persons': [],
                'key_person_bios': [],
                'location_name': '未知地点',
                'location_context': '',
                'event_date': article.get('date', ''),
                'curated_angles': [],
                'url': article.get('url', ''),
                'date': article.get('date', ''),
                'site': article.get('site', ''),
                'ai_processed_at': datetime.now().isoformat(),
                'content_length': len(article.get('content_text', '')),
                'error': str(e)
            })
        
        # 即使失败也保存缓存，避免重复处理
        for processed_article in processed:
            url = processed_article.get('url', '')
            if url:
                save_ai_to_cache(url, processed_article)
        
        return processed


def _generate_markdown(articles: List[Dict], config: NewsConfig = None) -> str:
    """
    生成 Markdown 格式的新闻汇总内容
    
    Args:
        articles: 处理后的文章列表
        config: NewsConfig 配置对象（包含自定义标题等）
    
    Returns:
        Markdown 格式的新闻汇总文本
    """
    if not articles:
        return ''
    
    markdown_lines = []
    
    report_header = config.report_header if config and config.report_header else '# 户外运动新闻汇总\n'
    markdown_lines.append(report_header)
    markdown_lines.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
    markdown_lines.append(f'共收录 {len(articles)} 篇文章\n')
    
    source_sites = list(set(article.get('site') for article in articles if isinstance(article, dict) and article.get('site')))
    if source_sites:
        markdown_lines.append('\n## 搜索来源网站\n')
        for site in source_sites:
            markdown_lines.append(f'- {site}\n')
        markdown_lines.append('\n---\n')
    
    for i, article in enumerate(articles, 1):
        # 确保 article 是字典类型
        if not isinstance(article, dict):
            logger.warning(f"⚠️ 跳过非字典类型的文章: {type(article)}")
            continue
        
        # 确保必要字段存在
        chinese_title = article.get('chinese_title', '未知标题')
        markdown_lines.append(f'\n## {i}. {chinese_title}\n')
        
        if article.get('original_title') and article.get('original_title') != article.get('chinese_title'):
            markdown_lines.append(f'**原标题**: {article["original_title"]}\n')
        
        if article.get('date'):
            markdown_lines.append(f'**日期**: {article["date"]}\n')
        
        # 处理链接字段
        url = article.get('url', '未知链接')
        markdown_lines.append(f'**链接**: {url}\n')
        
        if article.get('event_date'):
            markdown_lines.append(f'**事件日期**: {article["event_date"]}\n')
        
        if article.get('location_name'):
            location_name = article["location_name"]
            location_context = article.get("location_context", "")
            if location_context:
                markdown_lines.append(f'**地点**: {location_name}。{location_context}\n')
            else:
                markdown_lines.append(f'**地点**: {location_name}\n')
        else:
            markdown_lines.append(f'**地点**: 无\n')
        
        if article.get('key_persons'):
            markdown_lines.append(f'**关键人物**:\n')
            key_persons = article['key_persons']
            key_person_bios = article.get('key_person_bios', [])
            for j, name in enumerate(key_persons):
                person_encoded = name.replace(' ', '+')
                search_url = f"https://www.google.com/search?q={person_encoded}+outdoor"
                bio = key_person_bios[j] if j < len(key_person_bios) else ''
                if bio:
                    markdown_lines.append(f'- [{name}]({search_url})：{bio}\n')
                else:
                    markdown_lines.append(f'- [{name}]({search_url})\n')
            markdown_lines.append('\n')
        else:
            markdown_lines.append(f'**关键人物**: 无\n')
        
        if article.get('curated_angles'):
            angles = article['curated_angles']
            markdown_lines.append(f'**选题推荐**:\n')
            for angle_item in angles:
                markdown_lines.append(f'  - {angle_item}\n')
        else:
            markdown_lines.append(f'**选题推荐**: 无\n')
        
        markdown_lines.append(f'\n**摘要**: {article["summary"]}\n')
        
        markdown_lines.append('\n---\n')
    
    return ''.join(markdown_lines)


def _parse_text_with_links(text):
    """
    [内部工具] 解析包含 Markdown 链接的文本
    输入: "点击 [这里](http://google.com) 查看"
    输出: 飞书 TextElement 结构数组
    """
    elements = []
    # 正则匹配 [text](url)
    pattern = re.compile(r'\[(.*?)\]\((.*?)\)')
    last_idx = 0
    
    for match in pattern.finditer(text):
        # 1. 添加链接前的普通文本
        if match.start() > last_idx:
            elements.append(TextElement(
                text_run=TextRun(content=text[last_idx:match.start()])
            ))
        
        # 2. 添加链接文本
        link_text = match.group(1)
        link_url = match.group(2)
        elements.append(TextElement.builder()
            .text_run(TextRun.builder()
                .content(link_text)
                .text_element_style(TextElementStyle.builder()
                    .link(Link.builder().url(link_url).build())
                    .build())
                .build())
            .build())
        last_idx = match.end()
    
    # 3. 添加剩余的文本
    if last_idx < len(text):
        elements.append(TextElement.builder()
            .text_run(TextRun.builder()
                .content(text[last_idx:])
                .build())
            .build())
        
    # 如果没有链接，直接返回纯文本
    if not elements:
        elements.append(TextElement.builder()
            .text_run(TextRun.builder()
                .content(text)
                .build())
            .build())
        
    return elements

def publish_feishu_report(report_title, markdown_content, chat_id, 
                          collaborator_openids: List[str] = None):
    """
    发布新闻汇总到飞书文档
    
    核心功能: 创建文档 -> 写入内容 -> 发送卡片
    
    Args:
        report_title: 新闻汇总标题
        markdown_content: Markdown 格式的新闻汇总内容
        chat_id: 飞书群组 ID
        collaborator_openids: 协作者 openid 列表（可选，优先于环境变量）
    
    Returns:
        飞书文档链接，失败返回 None
    """
    print(f"🚀 [Feishu] 准备发布文档: {report_title}")
    
    # 获取飞书客户端（自动清除代理）
    client = get_feishu_client()
    
    # =================================================
    # 步骤 1: 创建一个新的空白文档
    # =================================================
    try:
        create_req = CreateDocumentRequest.builder() \
            .request_body(CreateDocumentRequestBody.builder()
                .title(report_title)
                .build()) \
            .build()
            
        resp = client.docx.v1.document.create(create_req)
        
        if not resp.success():
            print(f"❌ 创建文档失败: {resp.code} - {resp.msg}")
            return None
            
        document_id = resp.data.document.document_id
        # 注意: 只有飞书国内版是 feishu.cn，国际版请改为 larksuite.com
        doc_url = f"https://feishu.cn/docx/{document_id}"
        print(f"✅ 文档创建成功: {doc_url}")

        # 优先使用传入的协作者列表，其次使用环境变量
        openids = collaborator_openids if collaborator_openids else []
        if not openids:
            env_openids = os.getenv("FEISHU_COLLABORATOR_OPENIDS", "")
            if env_openids:
                openids = [oid.strip() for oid in env_openids.split(",") if oid.strip()]
        
        collaborator_perm = os.getenv("FEISHU_COLLABORATOR_PERM", "edit")
        
        if openids:
            
            added_count = 0
            failed_count = 0
            
            for openid in openids:
                try:
                    add_req = CreatePermissionMemberRequest.builder() \
                        .token(document_id) \
                        .type("docx") \
                        .need_notification(False) \
                        .request_body(BaseMember.builder()
                            .member_type("openid")
                            .member_id(openid)
                            .perm(collaborator_perm)
                            .perm_type("container")
                            .type("user")
                            .build()) \
                        .build()
                    
                    add_resp = client.drive.v1.permission_member.create(add_req)
                    
                    if add_resp.success():
                        print(f"✅ 协作者添加成功: {openid}")
                        added_count += 1
                    else:
                        print(f"⚠️ 协作者添加失败: {openid} - {add_resp.msg}")
                        failed_count += 1
                        
                except Exception as e:
                    print(f"⚠️ 为 {openid} 添加协作者时出错: {e}")
                    failed_count += 1
            
            if added_count > 0:
                print(f"✅ 成功添加 {added_count} 个协作者，权限: {collaborator_perm}")
            if failed_count > 0:
                print(f"⚠️ {failed_count} 个协作者添加失败")

    except Exception as e:
        print(f"❌ 飞书 API 连接错误: {e}")
        return None

    # =================================================
    # 步骤 2: 使用飞书官方 API 将 Markdown 转换为 Blocks
    # =================================================
    print("🔄 正在将 Markdown 转换为飞书文档块...")
    
    # 调用飞书官方的 Markdown 转换 API
    convert_req = ConvertDocumentRequest.builder() \
        .request_body(ConvertDocumentRequestBody.builder()
            .content_type("markdown")
            .content(markdown_content)
            .build()) \
        .build()
    
    convert_resp = client.docx.v1.document.convert(convert_req)
    
    if not convert_resp.success():
        print(f"❌ Markdown 转换失败: {convert_resp.code} - {convert_resp.msg}")
        return None
    
    # 获取转换后的 blocks
    blocks = convert_resp.data.blocks
    first_level_block_ids = convert_resp.data.first_level_block_ids or []
    
    if not blocks:
        print("⚠️ 转换后的内容为空")
        return doc_url
    
    # 使用 first_level_block_ids 重新排序 blocks
    if first_level_block_ids:
        block_map = {b.block_id: b for b in blocks}
        ordered_blocks = []
        for block_id in first_level_block_ids:
            if block_id in block_map:
                ordered_blocks.append(block_map[block_id])
        # 添加不在 first_level_block_ids 中的 blocks
        for block in blocks:
            if block.block_id not in first_level_block_ids:
                ordered_blocks.append(block)
        blocks = ordered_blocks
    
    print(f"✅ Markdown 转换成功，共 {len(blocks)} 个 blocks")
    
    # =================================================
    # 步骤 3: 使用转换好的 blocks 写入文档内容
    # =================================================
    print("📝 正在写入文档内容...")
    
    try:
        # 将 blocks 分批写入，避免单次请求过大
        batch_size = 50
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i + batch_size]
            
            # 直接使用转换好的 block 对象
            batch_req = CreateDocumentBlockChildrenRequest.builder() \
                .document_id(document_id) \
                .block_id(document_id) \
                .request_body(CreateDocumentBlockChildrenRequestBody.builder()
                    .children(batch)
                    .build()) \
                .build()
            
            batch_resp = client.docx.v1.document_block_children.create(batch_req)
            
            if not batch_resp.success():
                print(f"⚠️ 批次写入失败 (批次 {i//batch_size + 1}): {batch_resp.code} - {batch_resp.msg}")
            else:
                print(f"✅ 批次写入成功 (批次 {i//batch_size + 1}): {len(batch)} 个 blocks")
        
        print(f"✅ 文档内容写入完成，共 {len(blocks)} 个 blocks")
            
    except Exception as e:
        print(f"⚠️ 写入文档内容时出错: {e}")
        print("📝 跳过内容写入，继续发送通知...")
        # 即使出错，也继续后续步骤

    # =================================================
    # 步骤 4: 发送富文本卡片消息
    # =================================================
    print(f"📤 正在推送到群组: {chat_id}")
    
    # 构造卡片 JSON
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🧗‍♂️ 户外资讯新闻汇总已生成"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"本期资讯已由 AI 整理完毕。\n**标题：** {report_title}\n**时间：** {os.getenv('TODAY', '本期')}"
                }
            },
            {
                "tag": "hr"
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "👉 点击阅读完整新闻汇总"},
                        "url": doc_url,
                        "type": "primary"
                    }
                ]
            }
        ]
    }

    # 发送请求
    msg_req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()

    try:
        msg_resp = client.im.v1.message.create(msg_req)
        
        if msg_resp.success():
            print("✅ 消息推送成功")
        else:
            print(f"⚠️ 消息推送失败: {msg_resp.code} - {msg_resp.msg}")
            print("📝 仍然返回文档URL...")
    except Exception as e:
        print(f"⚠️ 发送消息时出错: {e}")
        print("📝 仍然返回文档URL...")
    
    # 关键：始终返回文档URL，即使内容写入或消息推送失败
    print(f"🎉 飞书文档发布完成!")
    print(f"📄 文档链接: {doc_url}")
    return doc_url
