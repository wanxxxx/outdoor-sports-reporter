import os
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import date, timedelta

# 添加父目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_newsletter import (
    get_default_outdoor_config,
    run_newsletter_task,
    run_weekly_newsletter_task,
    run_quick_test
)
from newsletter_tools import NewsConfig


class TestRunNewsletter(unittest.TestCase):
    """测试 run_newsletter.py 模块"""
    
    def test_get_default_outdoor_config(self):
        """测试获取默认户外运动配置"""
        # 保存原始环境变量
        original_target_sites = os.environ.get('TARGET_SITES')
        original_rss_feeds = os.environ.get('RSS_FEEDS')
        original_openids = os.environ.get('FEISHU_COLLABORATOR_OPENIDS')
        
        try:
            # 设置测试环境变量
            os.environ['TARGET_SITES'] = 'https://example.com, https://test.com'
            os.environ['RSS_FEEDS'] = 'https://example.com=https://example.com/rss, https://test.com=https://test.com/rss'
            os.environ['FEISHU_COLLABORATOR_OPENIDS'] = 'openid1, openid2'
            
            # 获取配置
            config = get_default_outdoor_config()
            
            # 验证配置
            self.assertEqual(config.name, "outdoor_sports")
            self.assertEqual(config.target_sites, ["https://example.com", "https://test.com"])
            self.assertEqual(config.rss_feeds, {
                "https://example.com": "https://example.com/rss",
                "https://test.com": "https://test.com/rss"
            })
            self.assertEqual(config.feishu_collaborator_openids, ["openid1", "openid2"])
            self.assertIn("户外运动周报", config.report_title_template)
            self.assertEqual(config.report_header, "# 户外运动周报\n")
            self.assertEqual(config.cache_prefix, "outdoor_")
            
        finally:
            # 恢复原始环境变量
            if original_target_sites:
                os.environ['TARGET_SITES'] = original_target_sites
            else:
                del os.environ['TARGET_SITES']
            
            if original_rss_feeds:
                os.environ['RSS_FEEDS'] = original_rss_feeds
            else:
                del os.environ['RSS_FEEDS']
            
            if original_openids:
                os.environ['FEISHU_COLLABORATOR_OPENIDS'] = original_openids
            else:
                del os.environ['FEISHU_COLLABORATOR_OPENIDS']
    
    def test_get_default_outdoor_config_empty_env(self):
        """测试环境变量为空时的默认配置"""
        # 保存原始环境变量
        original_target_sites = os.environ.get('TARGET_SITES')
        original_rss_feeds = os.environ.get('RSS_FEEDS')
        original_openids = os.environ.get('FEISHU_COLLABORATOR_OPENIDS')
        
        try:
            # 删除环境变量
            if 'TARGET_SITES' in os.environ:
                del os.environ['TARGET_SITES']
            if 'RSS_FEEDS' in os.environ:
                del os.environ['RSS_FEEDS']
            if 'FEISHU_COLLABORATOR_OPENIDS' in os.environ:
                del os.environ['FEISHU_COLLABORATOR_OPENIDS']
            
            # 获取配置
            config = get_default_outdoor_config()
            
            # 验证配置
            self.assertEqual(config.name, "outdoor_sports")
            self.assertEqual(config.target_sites, [])
            self.assertEqual(config.rss_feeds, {})
            self.assertEqual(config.feishu_collaborator_openids, [])
            
        finally:
            # 恢复原始环境变量
            if original_target_sites:
                os.environ['TARGET_SITES'] = original_target_sites
            if original_rss_feeds:
                os.environ['RSS_FEEDS'] = original_rss_feeds
            if original_openids:
                os.environ['FEISHU_COLLABORATOR_OPENIDS'] = original_openids
    
    @patch('run_newsletter.fetch_articles')
    @patch('run_newsletter.process_articles_with_ai')
    @patch('run_newsletter.publish_feishu_report')
    @patch('newsletter_tools.clean_all_expired_caches')
    def test_run_newsletter_task(self, mock_clean_cache, mock_publish, mock_process, mock_fetch):
        """测试通用新闻周报任务执行函数"""
        # 配置mock
        mock_clean_cache.return_value = None
        
        # 模拟抓取文章
        mock_articles = [
            {'site': 'https://example.com', 'url': 'https://example.com/article1', 'title': 'Test Article'}
        ]
        mock_fetch.return_value = mock_articles
        
        # 模拟AI处理
        mock_markdown = '# Test Report\n\n## 1. Test Article\n\n**摘要**: Test summary'
        mock_process.return_value = mock_markdown
        
        # 模拟发布
        mock_doc_url = 'https://feishu.cn/docx/test_doc_id'
        mock_publish.return_value = mock_doc_url
        
        # 创建配置
        config = NewsConfig(
            name="test_news",
            target_sites=["https://example.com"],
            rss_feeds={"https://example.com": "https://example.com/rss"},
            feishu_collaborator_openids=["openid1"]
        )
        
        # 调用函数
        result = run_newsletter_task(config, chat_id="test_chat_id", days_back=3)
        
        # 验证结果
        self.assertEqual(result, mock_doc_url)
        mock_clean_cache.assert_called_once()
        mock_fetch.assert_called_once()
        mock_process.assert_called_once_with(mock_articles, config=config)
        mock_publish.assert_called_once()
    
    @patch('run_newsletter.run_newsletter_task')
    def test_run_weekly_newsletter_task_backwards_compatible(self, mock_run_task):
        """测试向后兼容的 run_weekly_newsletter_task 函数"""
        # 配置mock
        mock_doc_url = 'https://feishu.cn/docx/test_doc_id'
        mock_run_task.return_value = mock_doc_url
        
        # 调用函数
        result = run_weekly_newsletter_task(chat_id="test_chat_id", days_back=3)
        
        # 验证结果
        self.assertEqual(result, mock_doc_url)
        mock_run_task.assert_called_once()
    
    @patch('json.load')
    @patch('os.path.exists')
    @patch('run_newsletter.process_articles_with_ai')
    @patch('run_newsletter.publish_feishu_report')
    def test_run_quick_test(self, mock_publish, mock_process, mock_exists, mock_json_load):
        """测试快速测试模式"""
        # 配置mock
        mock_exists.return_value = True
        
        # 模拟JSON加载
        mock_json_load.return_value = []
        
        # 模拟AI处理
        mock_markdown = '# Test Report\n\n## 1. Test Article\n\n**摘要**: Test summary'
        mock_process.return_value = mock_markdown
        
        # 模拟发布
        mock_doc_url = 'https://feishu.cn/docx/test_doc_id'
        mock_publish.return_value = mock_doc_url
        
        # 创建配置
        config = NewsConfig(
            name="test_news",
            feishu_collaborator_openids=["openid1"]
        )
        
        # 调用函数
        result = run_quick_test(config=config, chat_id="test_chat_id")
        
        # 验证结果
        self.assertEqual(result, mock_doc_url)
        mock_process.assert_called_once()
        mock_publish.assert_called_once()
    
    @patch('os.path.exists')
    def test_run_quick_test_file_not_exists(self, mock_exists):
        """测试快速测试模式 - 文件不存在"""
        # 配置mock
        mock_exists.return_value = False
        
        # 创建配置
        config = NewsConfig(name="test_news")
        
        # 调用函数
        result = run_quick_test(config=config)
        
        # 验证结果
        self.assertIsNone(result)
    
    @patch('json.load')
    @patch('os.path.exists')
    @patch('run_newsletter.process_articles_with_ai')
    def test_run_quick_test_ai_failure(self, mock_process, mock_exists, mock_json_load):
        """测试快速测试模式 - AI处理失败"""
        # 配置mock
        mock_exists.return_value = True
        
        # 模拟JSON加载
        mock_json_load.return_value = []
        
        # 模拟AI处理失败
        mock_process.return_value = ''
        
        # 创建配置
        config = NewsConfig(name="test_news")
        
        # 调用函数
        result = run_quick_test(config=config)
        
        # 验证结果
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
