import os
import json
import re
import hashlib
import pickle
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 保存原始代理设置
_original_proxy_settings = {
    'HTTP_PROXY': os.environ.get('HTTP_PROXY'),
    'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'),
    'ALL_PROXY': os.environ.get('ALL_PROXY')
}

# RSS缓存配置
RSS_CACHE_DIR = "cache/rss"
RSS_CACHE_TTL = 3600  # 1小时缓存

# AI处理缓存配置
AI_CACHE_DIR = "cache/ai"
AI_CACHE_TTL = 86400 * 7  # 7天缓存（AI处理结果长期有效）

# 创建缓存目录
os.makedirs(RSS_CACHE_DIR, exist_ok=True)
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
        logger.info(f"📦 AI缓存命中: {url}")
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
    并行抓取户外运动相关文章
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        max_workers: 最大并行工作线程数（仅用于网站级并发）
    
    Returns:
        文章列表
    """
    logger.info(f"🚀 开始并行抓取文章: {start_date} 到 {end_date}")
    
    # 确保网站抓取时使用代理
    enable_proxy_for_web_scraping()
    
    # 网站级并发：多个RSS源同时抓取，每个网站内部串行处理
    # 优化后的并发策略：max_workers=3 确保最多3个网站同时抓取
    # 每个网站内部的文章提取都是串行的，避免嵌套并发和连接池问题
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有抓取任务
        futures = []
        site_url_map = {}  # 记录future和对应网站URL的映射
        for site_url in TARGET_SITES:
            rss_feed = RSS_FEEDS.get(site_url)
            
            if rss_feed:
                future = executor.submit(_fetch_from_rss, rss_feed, site_url, start_date, end_date)
            else:
                future = executor.submit(_fetch_from_html, site_url, start_date, end_date)
            
            futures.append(future)
            site_url_map[id(future)] = site_url  # 记录映射关系
        
        # 收集结果
        articles = []
        completed = 0
        for future in as_completed(futures):
            try:
                site_result = future.result()
                current_site_url = site_url_map.get(id(future), "未知网站")
                
                # 处理不同类型的返回值
                if isinstance(site_result, dict):
                    # _fetch_from_html 返回字典
                    site_articles = site_result.get('articles', [])
                    articles.extend(site_articles)
                elif isinstance(site_result, list):
                    # _fetch_from_rss 返回列表
                    articles.extend(site_result)
                else:
                    logger.warning(f"⚠️ 未知返回类型: {type(site_result)}")
                
                completed += 1
                logger.info(f"✅ 完成 {completed}/{len(TARGET_SITES)} 个网站：{current_site_url}")
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
    
    # 为HTML抓取创建专门的requests会话，绕过全局代理清除
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    # 创建新的会话，不继承之前的代理设置
    session = requests.Session()
    
    # 备份并恢复代理环境变量
    original_env_backup = os.environ.copy()
    
    try:
        # 恢复代理设置以支持需要VPN的网站
        enable_proxy_for_web_scraping()
        
        # 设置会话信任环境变量（重要！）
        session.trust_env = True
        
        # 发送请求
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
                        'date': None  # HTML抓取可能没有具体日期
                    })
                    result['statistics']['successful_extraction'] += 1
                else:
                    # 内容提取失败，但保留链接信息
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
        
    except Exception as e:
        error_msg = f"HTML抓取失败 {site_url}: {str(e)}"
        logger.error(f"❌ {error_msg}")
        result['statistics']['error_messages'].append(error_msg)
    
    finally:
        # 关闭会话
        session.close()
        # 恢复环境变量状态
        os.environ.clear()
        os.environ.update(original_env_backup)
    
    return result


def _extract_content_with_session(url: str, session: requests.Session) -> Optional[str]:
    """使用指定会话提取内容，用于支持VPN代理"""
    try:
        # 设置请求头，模拟浏览器访问
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 使用指定会话发送请求
        response = session.get(url, headers=headers, timeout=15)
        
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



def process_articles_with_ai(articles_list: List[Dict], max_workers: int = 10, batch_size: int = 3) -> str:
    """
    批量并行处理文章并生成Markdown（支持缓存）
    
    Args:
        articles_list: 文章列表
        max_workers: 最大并行工作线程数
        batch_size: 每个批量处理的文章数量（建议3-5篇）
    
    Returns:
        Markdown格式的周报文本
    """
    if not articles_list:
        return ''
    
    # 首先筛选出需要AI处理的文章（缓存未命中）
    cached_articles = []
    articles_to_process = []
    
    for article in articles_list:
        url = article.get('url', '')
        cached_result = load_ai_from_cache(url)
        if cached_result:
            logger.info(f"🚀 AI缓存命中: {url}")
            cached_articles.append(cached_result)
        else:
            articles_to_process.append(article)
    
    logger.info(f"📊 AI缓存统计: {len(cached_articles)}篇命中缓存, {len(articles_to_process)}篇需要AI处理")
    
    # 如果所有文章都有缓存，直接生成Markdown
    if not articles_to_process:
        logger.info("✅ 所有文章均命中缓存，跳过AI处理")
        return _generate_markdown(cached_articles)
    
    try:
        client = _get_openai_client()
    except Exception as e:
        logger.error(f"初始化AI客户端失败: {str(e)}")
        # 如果有缓存的文章，仍然返回缓存结果
        if cached_articles:
            return _generate_markdown(cached_articles)
        return ''
    
    logger.info(f"🚀 开始批量并行AI处理: {len(articles_to_process)} 篇文章")
    logger.info(f"🤖 AI批量设置: batch_size={batch_size}, max_workers={max_workers}")
    
    # 将需要处理的文章分批
    batches = [articles_to_process[i:i + batch_size] for i in range(0, len(articles_to_process), batch_size)]
    logger.info(f"🤖 分为 {len(batches)} 个批次进行并行处理")
    
    newly_processed_articles = []
    completed = 0
    
    # 并行处理批次
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有批量处理任务
        futures = []
        batch_index_map = {}  # 记录future和对应批次数的映射
        for i, batch in enumerate(batches, 1):
            future = executor.submit(_process_batch_with_ai, client, batch, i)
            futures.append(future)
            batch_index_map[id(future)] = i  # 记录映射关系
        
        # 收集结果
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
    
    # 合并缓存结果和新处理的结果
    all_processed_articles = cached_articles + newly_processed_articles
    logger.info(f"🎉 批量并行AI处理完成: {len(cached_articles)}篇来自缓存, {len(newly_processed_articles)}篇新处理, 总计{len(all_processed_articles)}篇")
    
    markdown_text = _generate_markdown(all_processed_articles)
    
    return markdown_text


def _process_batch_with_ai(client: OpenAI, batch: List[Dict], batch_index: int) -> List[Dict]:
    """
    批量处理文章（一次处理多篇文章，支持缓存）
    
    Args:
        client: OpenAI客户端
        batch: 文章批次
        batch_index: 批次索引
    
    Returns:
        处理后的文章列表
    """
    if not batch:
        return []
    
    logger.info(f"🔄 处理批次 {batch_index}: {len(batch)} 篇文章")
    
    # 首先检查每篇文章的缓存
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
    
    # 如果所有文章都有缓存，直接返回
    if not articles_to_process:
        logger.info(f"✅ 批次 {batch_index} 全部命中缓存")
        return [result for _, result in cached_results]
    
    # 如果有部分文章需要处理，构建prompt
    if len(articles_to_process) < len(batch):
        logger.info(f"📦 批次 {batch_index}: {len(cached_results)}篇命中缓存, {len(articles_to_process)}篇需要AI处理")
    
    # 构建批量处理的prompt（只处理未缓存的文章）
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
    
    prompt = f"""
# Role
你是一名资深的**户外极限运动编辑 + 专注于“户外文化观察”和“影像美学”的自媒体（文章/播客）**，精通登山、攀岩、徒步等领域的专业知识和术语。你的任务是批量处理多篇文章，提取每篇文章的核心信息并生成周报素材。

# Input Data
以下是 {len(batch)} 篇户外运动相关文章，请逐个分析：

{batch_content}

# Goals
请为每篇文章提取以下信息，严格按照JSON格式返回：

对于每篇文章，返回以下结构的JSON对象：
{{
    "chinese_title": "对标题进行中文翻译）",
    "summary": "核心事件概括（人物+地点+成就），要求使用原文语言",
    "chinese_summary": "若summary为中文则赋值为summary；否则，对summary进行中文翻译", 
    "key_persons": ["关键人物1", "关键人物2"],
    "location": "事件地点，使用原文。无则返回空",
    "event_date": "事件时间",
    "key_person_bios": {{
        "相关人物英文原名": "一句话中文深度简介（背景、成就、风格）"
    }},
    "location_context": "事件地点介绍",
    "curated_angles": {{
        "选题角度1": "选题内容"
    }}
}}

# Output Format
翻译时，注意户外运动专业术语的翻译
必须返回纯净的JSON数组格式，严禁使用Markdown代码块。
key_persons，使用原文人名，不得进行翻译
key_person_bios，要求对key_persons的每个人物，用一句话中文进行简介（背景、成就、风格）
location_context：如果没有事件地点则为空。如果事件地点是山峰或攀岩线路，必须补充其攀登历史、首攀信息以及难度等级等；如果是普通地点，补充其地理或户外文化背景。",
curated_angles：请为用户生成3个深度选题角度。
   - **思考维度**：请从“影像美学”、“探险伦理”、“商业与纯粹的冲突”、“人物内心”、“极限运动的社会隐喻”等角度发散。
   - **格式要求**：每个角度请用【标签】：具体描述的形式。
   - **示例**：
     - "影像分析：分析摄影师 Jimmy Chin 如何利用广角镜头表现 Meru 鲨鱼鳍的压迫感"
     - "文化观察：从这次商业登山事故，看‘保姆式登山’对阿肯色州探险文化的侵蚀"
     - "播客话题：当赞助商要求‘必须登顶’时，攀登者的心理博弈"

"""
    
    try:
        model_name = os.getenv('LLM_MODEL')
        if not model_name:
            raise ValueError('LLM_MODEL environment variable is not set')

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {'role': 'system', 'content': '你是一个专业的户外新闻方向的文章分析助手，擅长批量提取文章关键信息并进行中英文翻译。'},
                {'role': 'user', 'content': prompt}
            ],
            temperature=0.3,
            response_format={'type': 'json_object'},
            timeout=60  # 批量处理需要更长时间
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # 解析JSON结果
        import json
        results = json.loads(result_text)
        
        # 确保结果是数组格式
        if isinstance(results, dict) and 'articles' in results:
            results = results['articles']
        elif not isinstance(results, list):
            logger.warning(f"批次 {batch_index} 返回格式异常，尝试修复...")
            results = [results] if not isinstance(results, list) else results
        
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
                    'chinese_summary': result.get('chinese_summary', result.get('summary', article.get('content_text', '')[:200] + '...')),
                    'key_persons': result.get('key_persons', []),
                    'key_person_bios': result.get('key_person_bios', {}),
                    'location': result.get('location', '未知地点'),
                    'location_context': result.get('location_context', ''),
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
                'chinese_summary': article.get('content_text', '')[:200] + '...',
                'key_persons': [],
                'location': '未知地点',
                'event_date': '',
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


def _generate_markdown(articles: List[Dict]) -> str:
    if not articles:
        return ''
    
    markdown_lines = []
    markdown_lines.append('# 户外运动周报\n')
    markdown_lines.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
    markdown_lines.append(f'共收录 {len(articles)} 篇文章\n')
    
    # 提取搜索的网站列表
    source_sites = list(set(article.get('site') for article in articles if isinstance(article, dict) and article.get('site')))
    if source_sites:
        markdown_lines.append('\n## 搜索来源网站\n')
        for site in source_sites:
            markdown_lines.append(f'- {site}\n')
        markdown_lines.append('\n---\n')
    
    for i, article in enumerate(articles, 1):
        markdown_lines.append(f'\n## {i}. {article["chinese_title"]}\n')
        
        if article.get('original_title') and article.get('original_title') != article.get('chinese_title'):
            markdown_lines.append(f'**原标题**: {article["original_title"]}\n')
        
        if article.get('date'):
            markdown_lines.append(f'**日期**: {article["date"]}\n')
        
        markdown_lines.append(f'**链接**: {article["url"]}\n')
        
        if article.get('key_persons'):
            persons_text = '、'.join(article['key_persons'])
            markdown_lines.append(f'**关键人物**: {persons_text}\n')
            
            # 为每个关键人物生成搜索链接
            for person in article['key_persons']:
                person_encoded = person.replace(' ', '+')
                search_url = f"https://www.google.com/search?q={person_encoded}+outdoor"
                markdown_lines.append(f'- [{person}]({search_url})\n')
            
            if article.get('key_person_bios'):
                for person_name, bio_text in article['key_person_bios'].items():
                    markdown_lines.append(f'  - **{person_name}**: {bio_text}\n')
            else:
                markdown_lines.append(f'  - **人物简介**: 无\n')
        else:
            markdown_lines.append(f'**关键人物**: 无\n')
        
        if article.get('location_context'):
            markdown_lines.append(f'**地点背景与历史**: {article["location_context"]}\n')
        else:
            markdown_lines.append(f'**地点背景与历史**: 无\n')
        
        if article.get('curated_angles'):
            angles = article['curated_angles']
            if isinstance(angles, dict):
                angles_list = list(angles.values())
            else:
                angles_list = angles
            markdown_lines.append(f'**选题策划角度**:\n')
            for angle in angles_list:
                markdown_lines.append(f'  - {angle}\n')
        else:
            markdown_lines.append(f'**选题策划角度**: 无\n')
        
        markdown_lines.append(f'\n**摘要**: {article["summary"]}\n')
        
        if article.get('chinese_summary') and article.get('chinese_summary') != article.get('summary'):
            markdown_lines.append(f'\n*中文摘要*: {article["chinese_summary"]}\n')
        
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

def publish_feishu_report(report_title, markdown_content, chat_id):
    """
    核心功能: 创建文档 -> 写入内容 -> 发送卡片
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

        collaborator_openids = os.getenv("FEISHU_COLLABORATOR_OPENIDS", "")
        collaborator_perm = os.getenv("FEISHU_COLLABORATOR_PERM", "edit")
        
        if collaborator_openids:
            openids = [oid.strip() for oid in collaborator_openids.split(",") if oid.strip()]
            
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
            "title": {"tag": "plain_text", "content": "🧗‍♂️ 户外资讯周报已生成"},
            "template": "blue" # 标题背景色: blue, wathet, turquoise, green, yellow, orange, red, carmine, violet, purple, indigo, grey
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"本周资讯已由 AI 整理完毕。\n**标题：** {report_title}\n**时间：** {os.getenv('TODAY', '本周')}"
                }
            },
            {
                "tag": "hr" # 分割线
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "👉 点击阅读完整周报"},
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
    # 测试需要，暂时注释发送飞书群组代码
    # try:
    #     msg_resp = client.im.v1.message.create(msg_req)
        
    #     if msg_resp.success():
    #         print("✅ 消息推送成功")
    #     else:
    #         print(f"⚠️ 消息推送失败: {msg_resp.code} - {msg_resp.msg}")
    #         print("📝 仍然返回文档URL...")
    # except Exception as e:
    #     print(f"⚠️ 发送消息时出错: {e}")
    #     print("📝 仍然返回文档URL...")
    
    # 关键：始终返回文档URL，即使内容写入或消息推送失败
    print(f"🎉 飞书文档发布完成!")
    print(f"📄 文档链接: {doc_url}")
    return doc_url
