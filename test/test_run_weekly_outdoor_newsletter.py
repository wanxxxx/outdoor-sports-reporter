import os
import sys
import unittest
from unittest.mock import Mock, patch
from datetime import date, timedelta

# 添加父目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_weekly_outdoor_newsletter import (
    get_outdoor_news_config,
    run_weekly_outdoor_newsletter_task,
    run_outdoor_quick_test
)
from newsletter_tools import NewsConfig


class TestRunWeeklyOutdoorNewsletter(unittest.TestCase):
    """测试 run_weekly_outdoor_newsletter.py 模块"""
    
    def test_get_outdoor_news_config(self):
        """测试获取户外运动新闻配置"""
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
            config = get_outdoor_news_config()
            
            # 验证配置
            self.assertEqual(config.name, "户外运动")
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
    
    def test_get_outdoor_news_config_empty_env(self):
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
            config = get_outdoor_news_config()
            
            # 验证配置
            self.assertEqual(config.name, "户外运动")
            self.assertEqual(config.target_sites, [])
            self.assertEqual(config.rss_feeds, {})
            self.assertEqual(config.feishu_collaborator_openids, [])
            self.assertIn("户外运动周报", config.report_title_template)
            self.assertEqual(config.report_header, "# 户外运动周报\n")
            self.assertEqual(config.cache_prefix, "outdoor_")
            
        finally:
            # 恢复原始环境变量
            if original_target_sites:
                os.environ['TARGET_SITES'] = original_target_sites
            if original_rss_feeds:
                os.environ['RSS_FEEDS'] = original_rss_feeds
            if original_openids:
                os.environ['FEISHU_COLLABORATOR_OPENIDS'] = original_openids
    
    @patch('run_weekly_outdoor_newsletter.get_outdoor_news_config')
    @patch('run_weekly_outdoor_newsletter.run_newsletter_task')
    def test_run_weekly_outdoor_newsletter_task(self, mock_run_task, mock_get_config):
        """测试运行户外运动周报生成和发布任务"""
        # 配置mock
        mock_config = Mock(spec=NewsConfig)
        mock_config.name = "户外运动"
        mock_get_config.return_value = mock_config
        
        mock_doc_url = 'https://feishu.cn/docx/test_doc_id'
        mock_run_task.return_value = mock_doc_url
        
        # 调用函数
        result = run_weekly_outdoor_newsletter_task(chat_id="test_chat_id", days_back=3)
        
        # 验证结果
        self.assertEqual(result, mock_doc_url)
        mock_get_config.assert_called_once()
        mock_run_task.assert_called_once_with(mock_config, chat_id="test_chat_id", days_back=3)
    
    @patch('run_weekly_outdoor_newsletter.get_outdoor_news_config')
    @patch('run_weekly_outdoor_newsletter.run_quick_test')
    def test_run_outdoor_quick_test(self, mock_run_test, mock_get_config):
        """测试户外运动快速测试模式"""
        # 配置mock
        mock_config = Mock(spec=NewsConfig)
        mock_config.name = "户外运动"
        mock_get_config.return_value = mock_config
        
        mock_doc_url = 'https://feishu.cn/docx/test_doc_id'
        mock_run_test.return_value = mock_doc_url
        
        # 调用函数
        result = run_outdoor_quick_test(chat_id="test_chat_id")
        
        # 验证结果
        self.assertEqual(result, mock_doc_url)
        mock_get_config.assert_called_once()
        mock_run_test.assert_called_once_with(config=mock_config, chat_id="test_chat_id")


if __name__ == "__main__":
    unittest.main()
