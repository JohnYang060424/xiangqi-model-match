#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
referee.py 规则引擎回归测试（纯标准库 unittest）。

覆盖：开局合法着法数、蹩马腿、塞象眼、炮架、将死、困毙、子力不足、
将帅对脸（含造成对脸的走法判非法）、走法格式/越界/己方子等基础校验。

运行：
  python -m unittest discover tests -v        # 仓库根目录
  python tests/test_referee.py                 # 直接运行
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import referee as R


def moves_set(board, color):
    """把合法着法转成 (sx,sy,tx,ty) 集合，便于断言包含/排除。"""
    return {(sx, sy, tx, ty) for sx, sy, tx, ty in R.legal_moves(board, color)}


class TestInitialPosition(unittest.TestCase):
    """标准开局：双方合法着法数应相等且为 44（中国象棋开局公认值）。"""

    def test_initial_legal_move_count(self):
        b = R.make_board()
        self.assertEqual(len(R.legal_moves(b, 'r')), 44)
        self.assertEqual(len(R.legal_moves(b, 'b')), 44)

    def test_fen_roundtrip(self):
        b = R.make_board()
        b2 = R.board_from_fen(R.board_to_fen(b))
        self.assertEqual(b, b2)


class TestBasicApplyMove(unittest.TestCase):
    """apply_move 基础校验：合法/非法/格式/越界/己方子。"""

    def setUp(self):
        self.b = R.make_board()

    def test_legal_moves(self):
        self.assertTrue(R.apply_move(self.b, 'h2-e2')[0])   # 炮二平五
        self.assertTrue(R.apply_move(self.b, 'a0-a1')[0])   # 车一进一
        self.assertTrue(R.apply_move(self.b, 'b0-c2')[0])   # 马二进三

    def test_move_into_own_piece(self):
        ok, _, msg = R.apply_move(self.b, 'a0-b0')          # 车撞己方马
        self.assertFalse(ok)
        self.assertIn('己方', msg)

    def test_move_from_empty(self):
        ok, _, _ = R.apply_move(self.b, 'c2-e1')            # 起点无子
        self.assertFalse(ok)

    def test_bad_format(self):
        self.assertFalse(R.apply_move(self.b, 'a0-z9')[0])  # 列越界
        self.assertFalse(R.apply_move(self.b, 'abc')[0])    # 格式错误


class TestHorseLeg(unittest.TestCase):
    """蹩马腿：马(4,4) 的腿点 (5,4) 被己方兵占，则 (6,3)/(6,5) 不可走。"""

    FEN = '9/9/9/9/9/4HP4/9/9/9/4K4'   # H(4,4) P(5,4) K(4,0)

    def setUp(self):
        self.b = R.board_from_fen(self.FEN)
        self.m = moves_set(self.b, 'r')

    def test_blocked_leg_moves_excluded(self):
        for mv in ((4, 4, 6, 3), (4, 4, 6, 5)):
            self.assertNotIn(mv, self.m, f'蹩马腿走法 {mv} 不应合法')

    def test_other_directions_free(self):
        free = {(4, 4, 2, 3), (4, 4, 2, 5), (4, 4, 3, 2),
                (4, 4, 3, 6), (4, 4, 5, 2), (4, 4, 5, 6)}
        self.assertTrue(free <= self.m)


class TestElephantEye(unittest.TestCase):
    """塞象眼：红相(2,0) 的眼点 (1,1) 被占，则不可到 (0,2)；(4,2) 不受影响。"""

    FEN = '9/9/9/9/9/9/9/9/1P7/2E1K1E2'   # P(1,1) E(2,0) E(6,0) K(4,0)

    def setUp(self):
        self.b = R.board_from_fen(self.FEN)
        self.m = moves_set(self.b, 'r')

    def test_eye_blocked(self):
        self.assertNotIn((2, 0, 0, 2), self.m)
        self.assertIn((2, 0, 4, 2), self.m)


class TestCannonScreen(unittest.TestCase):
    """炮吃子需恰好一个炮架；无炮架只能平移不能越子吃。"""

    def test_capture_with_screen(self):
        b = R.board_from_fen('9/9/9/9/9/1C1P1r3/9/9/9/4K4')  # C(1,4) P(3,4) r(5,4)
        self.assertIn((1, 4, 5, 4), moves_set(b, 'r'))

    def test_no_screen_no_capture(self):
        b = R.board_from_fen('9/9/9/9/9/1C3r3/9/9/9/4K4')    # C(1,4) r(5,4) 无架
        self.assertNotIn((1, 4, 5, 4), moves_set(b, 'r'))


class TestFacingGenerals(unittest.TestCase):
    """将帅对脸：无阻对脸双方都在被将状态；造成对脸的走法判非法。"""

    def test_facing_is_check(self):
        b = R.board_from_fen('4k4/9/9/9/9/9/9/9/9/4K4')      # K(4,0) k(4,9) 无阻
        self.assertTrue(R.in_check(b, 'r'))
        self.assertTrue(R.in_check(b, 'b'))

    def test_move_creating_facing_illegal(self):
        # 红帅(4,0) 红仕(4,1) 黑将(4,9)：士 e1-d2 离开中线 -> 对脸 -> 非法
        b = R.board_from_fen('4k4/9/9/9/9/9/9/9/4A4/4K4')
        ok, _, msg = R.apply_move(b, 'e1-d2')
        self.assertFalse(ok)
        self.assertIn('对脸', msg)

    def test_advisor_move_legal_when_not_facing(self):
        # 黑将在 (5,9) 不同列：士 e1-d2 合法
        b = R.board_from_fen('5k3/9/9/9/9/9/9/9/4A4/4K4')
        self.assertTrue(R.apply_move(b, 'e1-d2')[0])


class TestEndgame(unittest.TestCase):
    """终局判定：将死 / 困毙 / 子力不足。"""

    def test_checkmate(self):
        # 红帅(4,0) 被 r(0,0) 沿底线将，逃点 (3,0)(5,0) 仍被将、(4,1) 被 r(4,8) 将
        b = R.board_from_fen('9/4r4/9/9/9/9/9/9/9/r3K4')
        self.assertEqual(R.evaluate_state(b, 'r'), ('black_win', '将死(无应将)'))
        self.assertEqual(len(R.legal_moves(b, 'r')), 0)

    def test_stalemate(self):
        # 红帅(4,0)：逃点 (3,0)(4,1)(5,0) 分别被 h(2,2)/h(6,2) 控制，本身未被将
        b = R.board_from_fen('5k3/9/9/9/9/9/9/2h3h2/9/4K4')
        self.assertEqual(R.evaluate_state(b, 'r'), ('black_win', '困毙(无子可动)'))
        self.assertEqual(len(R.legal_moves(b, 'r')), 0)

    def test_insufficient_material(self):
        # 双方只有将士/将仕，无车马炮兵卒 -> 和
        b = R.board_from_fen('3ak4/9/9/9/9/9/9/9/9/3AK4')
        self.assertEqual(R.evaluate_state(b, 'r')[0], 'draw')

    def test_initial_position_playing(self):
        self.assertEqual(R.evaluate_state(R.make_board(), 'r')[0], 'playing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
