import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

# 添加父目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newsletter_tools import (
    NewsConfig,
    fetch_articles,
    fetch_outdoor_articles,
    _generate_markdown,
    publish_feishu_report
)


class TestNewsConfig(unittest.TestCase):
    """测试 NewsConfig 配置类"""
    
    def test_news_config_default_values(self):
        """测试默认值"""
        config = NewsConfig(name="test")
        self.assertEqual(config.name, "test")
        self.assertEqual(config.target_sites, [])
        self.assertEqual(config.rss_feeds, {})
        self.assertEqual(config.ai_prompt, "")
        self.assertIn("专业的新闻分析助手", config.ai_system_prompt)
        self.assertEqual(config.feishu_collaborator_openids, [])
        self.assertIn("{name}周报", config.report_title_template)
        self.assertEqual(config.report_header, "# 新闻周报\n")
        self.assertEqual(config.cache_prefix, "")
    
    def test_news_config_custom_values(self):
        """测试自定义值"""
        config = NewsConfig(
            name="outdoor",
            target_sites=["https://example.com"],
            rss_feeds={"https://example.com": "https://example.com/rss"},
            ai_prompt="Custom prompt",
            ai_system_prompt="Custom system prompt",
            feishu_collaborator_openids=["openid1", "openid2"],
            report_title_template="Custom {name} Report",
            report_header="# Custom Header\n",
            cache_prefix="outdoor_"
        )
        self.assertEqual(config.name, "outdoor")
        self.assertEqual(config.target_sites, ["https://example.com"])
        self.assertEqual(config.rss_feeds, {"https://example.com": "https://example.com/rss"})
        self.assertEqual(config.ai_prompt, "Custom prompt")
        self.assertEqual(config.ai_system_prompt, "Custom system prompt")
        self.assertEqual(config.feishu_collaborator_openids, ["openid1", "openid2"])
        self.assertEqual(config.report_title_template, "Custom {name} Report")
        self.assertEqual(config.report_header, "# Custom Header\n")
        self.assertEqual(config.cache_prefix, "outdoor_")


class TestFetchArticles(unittest.TestCase):
    """测试文章抓取功能"""
    
    @patch('newsletter_tools._fetch_from_html')
    def test_fetch_articles_with_config(self, mock_fetch_html):
        """测试使用配置对象抓取文章"""
        # 配置mock返回值
        mock_fetch_html.return_value = {
            'articles': [
                {'site': 'https://example.com', 'url': 'https://example.com/article1', 'title': 'Test Article'}
            ]
        }
        
        # 创建配置
        config = NewsConfig(
            name="test",
            target_sites=["https://example.com"]
        )
        
        # 调用函数
        start_date = date.today() - timedelta(days=7)
        end_date = date.today()
        articles = fetch_articles(start_date, end_date, config=config)
        
        # 验证结果
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]['title'], 'Test Article')
        mock_fetch_html.assert_called_once()
    
    @patch('newsletter_tools._fetch_from_html')
    def test_fetch_articles_with_direct_params(self, mock_fetch_html):
        """测试直接传入参数抓取文章"""
        mock_fetch_html.return_value = {
            'articles': [
                {'site': 'https://example.com', 'url': 'https://example.com/article1', 'title': 'Test Article'}
            ]
        }
        
        start_date = date.today() - timedelta(days=7)
        end_date = date.today()
        articles = fetch_articles(
            start_date, end_date,
            target_sites=["https://example.com"]
        )
        
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]['title'], 'Test Article')
        mock_fetch_html.assert_called_once()
    
    @patch('newsletter_tools.fetch_articles')
    def test_fetch_outdoor_articles_backwards_compatible(self, mock_fetch):
        """测试向后兼容的 fetch_outdoor_articles 函数"""
        # 配置mock
        mock_articles = []
        mock_fetch.return_value = mock_articles
        
        start_date = date.today() - timedelta(days=7)
        end_date = date.today()
        
        # 调用函数
        result = fetch_outdoor_articles(start_date, end_date, max_workers=1)
        
        # 验证结果
        assert isinstance(result, list)
        mock_fetch.assert_called_once()


class TestGenerateMarkdown(unittest.TestCase):
    """测试 Markdown 生成功能"""
    
    def test_generate_markdown_basic(self):
        """测试基本 Markdown 生成"""
        articles = [
            {
                'chinese_title': '测试文章',
                'original_title': 'Test Article',
                'date': '2024-01-01',
                'url': 'https://example.com/article',
                'event_date': '2024-01-01',
                'location_name': '测试地点',
                'location_context': '测试地点背景',
                'key_persons': ['Person 1'],
                'key_person_bios': ['Person 1 bio'],
                'curated_angles': ['【标签】测试角度'],
                'summary': '测试摘要'
            }
        ]
        
        markdown = _generate_markdown(articles)
        
        self.assertIn('# 户外运动周报', markdown)
        self.assertIn('测试文章', markdown)
        self.assertIn('测试摘要', markdown)
    
    def test_generate_markdown_with_config(self):
        """测试使用自定义配置生成 Markdown"""
        articles = [
            {
                'chinese_title': '测试文章',
                'summary': '测试摘要'
            }
        ]
        
        config = NewsConfig(
            name="test",
            report_header="# Custom Report\n"
        )
        
        markdown = _generate_markdown(articles, config=config)
        
        self.assertIn('# Custom Report', markdown)
        self.assertIn('测试文章', markdown)
    
    def test_generate_markdown_empty_articles(self):
        """测试空文章列表"""
        markdown = _generate_markdown([])
        self.assertEqual(markdown, '')
    
    def test_generate_markdown_with_non_dict_articles(self):
        """测试包含非字典类型文章的列表"""
        articles = [
            {
                'chinese_title': 'Valid Article',
                'summary': 'Valid summary'
            },
            'invalid article',  # 非字典类型
            {
                'chinese_title': 'Another Valid Article',
                'summary': 'Another valid summary'
            }
        ]
        
        markdown = _generate_markdown(articles)
        self.assertIn('Valid Article', markdown)
        self.assertIn('Another Valid Article', markdown)


class TestPublishFeishuReport(unittest.TestCase):
    """测试飞书发布功能"""
    
    @patch('newsletter_tools.get_feishu_client')
    def test_publish_feishu_report_with_custom_collaborators(self, mock_get_client):
        """测试使用自定义协作者列表"""
        # 配置mock
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        
        # 模拟创建文档响应
        mock_create_resp = Mock()
        mock_create_resp.success.return_value = True
        mock_create_resp.data.document.document_id = 'test_doc_id'
        mock_client.docx.v1.document.create.return_value = mock_create_resp
        
        # 模拟Markdown转换响应
        mock_convert_resp = Mock()
        mock_convert_resp.success.return_value = True
        mock_convert_resp.data.blocks = []
        mock_convert_resp.data.first_level_block_ids = []
        mock_client.docx.v1.document.convert.return_value = mock_convert_resp
        
        # 模拟添加协作者响应
        mock_add_resp = Mock()
        mock_add_resp.success.return_value = True
        mock_client.drive.v1.permission_member.create.return_value = mock_add_resp
        
        # 模拟发送消息响应
        mock_msg_resp = Mock()
        mock_msg_resp.success.return_value = True
        mock_client.im.v1.message.create.return_value = mock_msg_resp
        
        # 调用函数
        result = publish_feishu_report(
            "Test Report",
            "# Test Content",
            "test_chat_id",
            collaborator_openids=["openid1", "openid2"]
        )
        
        # 验证结果
        self.assertEqual(result, "https://feishu.cn/docx/test_doc_id")
        # 验证协作者添加被调用了2次
        self.assertTrue(mock_client.drive.v1.permission_member.create.called)
        self.assertEqual(mock_client.drive.v1.permission_member.create.call_count, 2)
    
    @patch('os.getenv')
    @patch('newsletter_tools.get_feishu_client')
    def test_publish_feishu_report_no_collaborators(self, mock_get_client, mock_getenv):
        """测试不指定协作者"""
        # 模拟环境变量返回空值
        def mock_env_get(key, default=None):
            if key == "FEISHU_COLLABORATOR_OPENIDS":
                return ""
            return default
        mock_getenv.side_effect = mock_env_get
        
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        
        mock_create_resp = Mock()
        mock_create_resp.success.return_value = True
        mock_create_resp.data.document.document_id = 'test_doc_id'
        mock_client.docx.v1.document.create.return_value = mock_create_resp
        
        mock_convert_resp = Mock()
        mock_convert_resp.success.return_value = True
        mock_convert_resp.data.blocks = []
        mock_convert_resp.data.first_level_block_ids = []
        mock_client.docx.v1.document.convert.return_value = mock_convert_resp
        
        mock_msg_resp = Mock()
        mock_msg_resp.success.return_value = True
        mock_client.im.v1.message.create.return_value = mock_msg_resp
        
        result = publish_feishu_report(
            "Test Report",
            "# Test Content",
            "test_chat_id"
        )
        
        self.assertEqual(result, "https://feishu.cn/docx/test_doc_id")
        # 验证协作者添加没有被调用
        self.assertFalse(mock_client.drive.v1.permission_member.create.called)
    
    @patch('newsletter_tools.get_feishu_client')
    def test_publish_feishu_report_create_document_failure(self, mock_get_client):
        """测试创建文档失败"""
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        
        mock_create_resp = Mock()
        mock_create_resp.success.return_value = False
        mock_client.docx.v1.document.create.return_value = mock_create_resp
        
        result = publish_feishu_report(
            "Test Report",
            "# Test Content",
            "test_chat_id"
        )
        
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
