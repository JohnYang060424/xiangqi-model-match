#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arena.py 的解析层回归测试（纯标准库 unittest）。

重点覆盖 v1.1.1 修复的 parse_move 思考标签泛化：
旧实现只认 Qwen 服务端特有的 </think:6124c78e>，换模型（</think>、</reasoning>、
</output>）即失效，思考链里的坐标会被误解析为走法。

运行：
  python -m unittest tests.test_arena -v
  python tests/test_arena.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import arena


class TestParseMove(unittest.TestCase):
    """从模型原始输出抽取 from-to 坐标。"""

    def test_plain_move(self):
        self.assertEqual(arena.parse_move('h2-e2'), 'h2-e2')

    def test_qwen_think_tag(self):
        # 旧实现唯一支持的闭合标签，回归不能退化
        text = '<think>黑方子力占优…</think:6124c78e>\nc6-c5'
        self.assertEqual(arena.parse_move(text), 'c6-c5')

    def test_standard_think_tag(self):
        # 标准 </think>（此前匹配不到 -> 思考里的坐标被误取）
        text = '<think>考虑 b9-c7 或 g9-f7…</think>\nb9-c7'
        self.assertEqual(arena.parse_move(text), 'b9-c7')

    def test_reasoning_tag(self):
        text = '分析：d7-d6 不错\n</reasoning>\ne3-e2'
        self.assertEqual(arena.parse_move(text), 'e3-e2')

    def test_output_tag(self):
        text = '<output>\nh0-g0\n</output>'
        self.assertEqual(arena.parse_move(text), 'h0-g0')
        # 闭合标签后仅剩空白：答案在 output 块内，应回退取块内坐标
        text2 = '<output>\nh0-g0\n</output>\n'
        self.assertEqual(arena.parse_move(text2), 'h0-g0')

    def test_truncated_think_falls_back_to_last(self):
        # 思考被截断、无闭合标签：退化取整段最后一个坐标
        text = '<think>考虑 a1-a2 与 b1-b2，其中 b1-b2 更'
        self.assertEqual(arena.parse_move(text), 'b1-b2')

    def test_piece_prefix_and_noise(self):
        self.assertEqual(arena.parse_move('走 R h2-e2'), 'h2-e2')
        self.assertEqual(arena.parse_move('(h2-e2)'), 'h2-e2')

    def test_no_move(self):
        self.assertIsNone(arena.parse_move('我不知道'))
        self.assertIsNone(arena.parse_move(''))


class TestDetectResign(unittest.TestCase):
    def test_resign_variants(self):
        for t in ('RESIGN', '我认输', '认输', '投降'):
            self.assertTrue(arena.detect_resign(t), t)
        self.assertFalse(arena.detect_resign('h2-e2'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
