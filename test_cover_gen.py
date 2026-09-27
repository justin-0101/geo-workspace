"""封面生成与封面接入的回归测试。

钉住两个真实发生过的失败：

1. 走公众号发布时**从未带封面**，微信回一个 40007 invalid media_id ——
   看报错完全看不出根因；
2. 封面不能依赖外部生图额度：MiniMax 额度用尽时必须仍然出得了图，
   否则自动化会退化成「每次都人工补封面」。
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cover_gen


def _draw():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new('RGB', cover_gen.CANVAS))


class CoverGenTest(unittest.TestCase):
    def test_renders_wechat_sized_jpeg(self):
        with TemporaryDirectory() as tmp:
            path = cover_gen.build('设备资产管理系统的核心功能：从台账到数据资产',
                                   '制造企业设备资产分散、台账与实物对不上', out_dir=tmp)
            self.assertTrue(path.exists())
            from PIL import Image
            with Image.open(path) as image:
                self.assertEqual(image.size, cover_gen.CANVAS)
                self.assertEqual(image.format, 'JPEG')
            # 微信素材上限 2MB；封面若接近上限说明压得不对
            self.assertLess(path.stat().st_size, 2 * 1024 * 1024)

    def test_no_external_api_dependency(self):
        """出图过程不发起任何网络请求 —— 生图额度断了也得出得了封面。"""
        from unittest.mock import patch
        with TemporaryDirectory() as tmp, patch('socket.socket.connect',
                                                side_effect=AssertionError('封面生成不应联网')):
            self.assertTrue(cover_gen.build('标题', out_dir=tmp).exists())

    def test_same_title_renders_same_bytes(self):
        with TemporaryDirectory() as tmp:
            first = cover_gen.build('同一篇标题', out_dir=tmp).read_bytes()
            second = cover_gen.build('同一篇标题', out_dir=tmp).read_bytes()
            self.assertEqual(first, second)

    def test_long_title_still_fits_canvas(self):
        with TemporaryDirectory() as tmp:
            path = cover_gen.build('设备资产管理系统在制造业现场怎么用起来' * 6, out_dir=tmp)
            self.assertTrue(path.exists())

    def test_empty_title_is_rejected(self):
        with self.assertRaises(cover_gen.CoverError):
            cover_gen.build('   ')

    def test_title_avoids_single_character_widow(self):
        """末行只剩一两个字（…从台账到数据 / 资产）是中文海报最难看的情况。"""
        max_width = cover_gen.CANVAS[0] - cover_gen.MARGIN * 2
        _, lines = cover_gen._fit_title(_draw(), '设备资产管理系统的核心功能：从台账到数据资产', max_width)
        self.assertGreater(len(lines), 1)
        self.assertGreater(len(lines[-1]), 2)

    def test_closing_punctuation_never_starts_a_line(self):
        lines = cover_gen.wrap_lines(
            _draw(), '点检、保养、备件消耗：制造企业现场怎么用起来，看这一份清单就够',
            cover_gen._font(48), 400)
        self.assertGreater(len(lines), 1)
        for line in lines[1:]:
            self.assertNotIn(line[0], cover_gen.NO_LINE_START)

    def test_palette_is_stable_for_same_text(self):
        self.assertEqual(cover_gen.pick_palette('设备资产管理'), cover_gen.pick_palette('设备资产管理'))

    def test_existing_local_cover_is_used_as_is(self):
        with TemporaryDirectory() as tmp:
            local = Path(tmp) / 'mine.jpg'
            local.write_bytes(b'local-cover')
            self.assertEqual(cover_gen.ensure_cover({'title': '标题', 'cover': str(local)}), local)

    def test_asset_without_cover_gets_one_generated(self):
        with TemporaryDirectory() as tmp:
            got = cover_gen.ensure_cover({'id': 'abc123', 'title': '标题'}, tmp)
            self.assertEqual(got.parent, Path(tmp))
            self.assertEqual(got.name, 'abc123.jpg')
            self.assertTrue(got.exists())

    def test_broken_cover_field_is_reported_not_silently_ignored(self):
        with self.assertRaises(cover_gen.CoverError) as ctx:
            cover_gen.resolve_cover('封面.png')
        self.assertIn('封面.png', str(ctx.exception))

    def test_blank_cover_field_raises(self):
        with self.assertRaises(cover_gen.CoverError):
            cover_gen.resolve_cover('   ')

    def test_wechat_adapter_generates_uploads_and_passes_cover(self):
        """公众号适配器的三步链路：生成封面 → 永久素材 → thumb_media_id 写草稿。"""
        from unittest.mock import MagicMock, patch
        import publish_adapters

        fake_client = MagicMock()
        fake_client.upload_thumb.return_value = 'THUMB-GENERATED'
        fake_client.add_draft.return_value = {'media_id': 'DRAFT-GENERATED'}
        asset = {
            'id': 'asset-cover-test',
            'title': '设备资产管理系统的核心功能',
            'body': '正文',
            'brief': '从台账到数据',
            'cover': '',
        }
        with TemporaryDirectory() as tmp:
            with (patch('publish_adapters.credentials.get', return_value='configured'),
                  patch('wechat_mp.WechatMpClient', return_value=fake_client)):
                result = publish_adapters.get_adapter('wechat_mp').submit_api(asset, Path(tmp))

        self.assertEqual(result.status, 'draft_created')
        self.assertIn('封面', result.message)
        self.assertTrue(result.evidence)
        fake_client.upload_thumb.assert_called_once()
        fake_client.add_draft.assert_called_once()
        kwargs = fake_client.add_draft.call_args.kwargs
        self.assertEqual(kwargs['thumb_media_id'], 'THUMB-GENERATED')


if __name__ == '__main__':
    unittest.main()
