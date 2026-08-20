#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国象棋裁判引擎（规则校验收官机）
坐标: 列 a-i（红方视角从左到右）, 行 0-9（红底线=0, 黑底线=9）
棋子: 红 R车 H马 E相 A仕 K帅 C炮 P兵 ; 黑 r h e a k c p（小写）
走子: 例如 h2-e2（红炮平中路）, e0-e1（帅上一步）

用法:
  python referee.py init                 # 输出初始局面 + FEN
  python referee.py fen <FEN>            # 从FEN载入并输出局面
  python referee.py move <from>-<to>     # 尝试走子, 输出校验结果
  python referee.py legal <color>        # 列出某方全部合法着法
  python referee.py resign <color>       # 某方认输

任何命令都会 stdout 完整局面(FEN+ASCII棋盘+状态)。退出码 0=成功。
"""
import sys

EMPTY = '.'

# ---- 九宫 ----
def in_palace(x, y, color):
    if color == 'r':  # 红帅在下 (row 0..2)
        return 3 <= x <= 5 and 0 <= y <= 2
    else:             # 黑将在上 (row 7..9)
        return 3 <= x <= 5 and 7 <= y <= 9

def own_side(x, y, color):
    return y <= 4 if color == 'r' else y >= 5

def crossed_river(y, color):
    return y >= 5 if color == 'r' else y <= 4

def is_red(ch):
    return ch != EMPTY and ch.isupper()

def is_black(ch):
    return ch != EMPTY and ch.islower()

def make_board():
    """标准初始局面, board[y][x], y=0 为红底线"""
    b = [[EMPTY]*9 for _ in range(10)]
    # 黑 (top)
    for x, ch in zip(range(9), 'rheakaehr'):
        b[9][x] = ch
    b[7][1] = b[7][7] = 'c'
    for x in (0, 2, 4, 6, 8):
        b[6][x] = 'p'
    # 红 (bottom)
    for x, ch in zip(range(9), 'RHEAKAEHR'):
        b[0][x] = ch
    b[2][1] = b[2][7] = 'C'
    for x in (0, 2, 4, 6, 8):
        b[3][x] = 'P'
    return b

def board_to_fen(b):
    rows = []
    for y in range(9, -1, -1):
        empty = 0
        row = ''
        for x in range(9):
            ch = b[y][x]
            if ch == EMPTY:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += ch
        if empty:
            row += str(empty)
        rows.append(row)
    return '/'.join(rows)

def board_from_fen(fen):
    b = [[EMPTY]*9 for _ in range(10)]
    rows = fen.strip().split('/')
    assert len(rows) == 10, f'FEN 必须有10行: {fen}'
    for i, row in enumerate(rows):
        y = 9 - i
        x = 0
        for ch in row:
            if ch.isdigit():
                x += int(ch)
            else:
                b[y][x] = ch
                x += 1
    return b

def find_king(b, color):
    k = 'K' if color == 'r' else 'k'
    for y in range(10):
        for x in range(9):
            if b[y][x] == k:
                return (x, y)
    return None

def onsame_file_no_block(b, x, y1, y2):
    """同一列 x, y1..y2 之间(不含端点)是否有子"""
    lo, hi = min(y1, y2), max(y1, y2)
    for y in range(lo+1, hi):
        if b[y][x] != EMPTY:
            return False
    return True

def kings_face(b):
    """将帅是否对脸(同列且中间无子)"""
    kr = find_king(b, 'r'); kb = find_king(b, 'b')
    if not kr or not kb:
        return False
    if kr[0] == kb[0] and onsame_file_no_block(b, kr[0], kr[1], kb[1]):
        return True
    return False

def attackers(b, x, y, color):
    """哪些 color 方的棋子能一步吃到 (x,y)。返回 [(sx,sy), ...]"""
    res = []
    def add(sx, sy):
        res.append((sx, sy))
    for sy in range(10):
        for sx in range(9):
            ch = b[sy][sx]
            if ch == EMPTY:
                continue
            if color == 'r' and not is_red(ch): continue
            if color == 'b' and not is_black(ch): continue
            if can_capture(b, sx, sy, x, y):
                add(sx, sy)
    return res

def can_capture(b, sx, sy, tx, ty):
    """(sx,sy) 的棋子按吃子规则能否到 (tx,ty)。调用方保证不同色。"""
    ch = b[sy][sx]
    color = 'r' if is_red(ch) else 'b'
    kind = ch.upper()
    if kind == 'R':  # 车: 直线无阻
        if sx == tx:
            return onsame_file_no_block(b, sx, sy, ty) and sy != ty
        if sy == ty:
            for x in range(min(sx,tx)+1, max(sx,tx)):
                if b[sy][x] != EMPTY: return False
            return sx != tx
        return False
    if kind == 'C':  # 炮: 吃子需恰好隔一子
        if sx == tx and sy != ty:
            cnt = 0
            for y in range(min(sy,ty)+1, max(sy,ty)):
                if b[y][sx] != EMPTY: cnt += 1
            return cnt == 1
        if sy == ty and sx != tx:
            cnt = 0
            for x in range(min(sx,tx)+1, max(sx,tx)):
                if b[sy][x] != EMPTY: cnt += 1
            return cnt == 1
        return False
    if kind == 'H':  # 马: 日字, 蹩马腿
        dx, dy = tx-sx, ty-sy
        if (abs(dx), abs(dy)) not in ((1,2),(2,1)):
            return False
        if abs(dx) == 2:
            leg = (sx + dx//2, sy)          # 蹩腿点: 水平方向相邻
        else:
            leg = (sx, sy + dy//2)          # 蹩腿点: 垂直方向相邻
        return b[leg[1]][leg[0]] == EMPTY
    if kind == 'E':  # 相/象: 田字, 不过河, 塞象眼
        dx, dy = tx-sx, ty-sy
        if (abs(dx), abs(dy)) != (2,2):
            return False
        if not own_side(tx, ty, color):
            return False
        eye = (sx+dx//2, sy+dy//2)
        return b[eye[1]][eye[0]] == EMPTY
    if kind == 'A':  # 仕/士: 九宫内斜一步
        if not in_palace(tx, ty, color):
            return False
        return abs(tx-sx) == 1 and abs(ty-sy) == 1
    if kind == 'K':  # 帅/将: 九宫内直一步; 对脸飞将视为可吃
        if abs(tx-sx) + abs(ty-sy) == 1 and in_palace(tx, ty, color):
            return True
        # 飞将: 同列无阻直接吃掉对方老将
        if tx == sx:
            k = 'k' if color == 'r' else 'K'
            if b[ty][tx] == k and onsame_file_no_block(b, sx, sy, ty):
                return True
        return False
    if kind == 'P':  # 兵/卒
        if color == 'r':
            if ty == sy+1 and tx == sx: return True
            if crossed_river(sy, 'r') and ty == sy and abs(tx-sx) == 1: return True
        else:
            if ty == sy-1 and tx == sx: return True
            if crossed_river(sy, 'b') and ty == sy and abs(tx-sx) == 1: return True
        return False
    return False

def pseudo_moves(b, color):
    """某方所有伪合法着法 [(sx,sy,tx,ty), ...] (未过滤己方被将)"""
    moves = []
    def try_add(sx, sy, tx, ty):
        if not (0 <= tx < 9 and 0 <= ty < 10):
            return
        tgt = b[ty][tx]
        if tgt != EMPTY:
            other = is_black(tgt) if color == 'r' else is_red(tgt)
            if not other:
                return  # 吃自己人
        else:
            tgt_any = False
        # 非吃子移动另查
        if tgt == EMPTY:
            if can_move_to(b, sx, sy, tx, ty):
                moves.append((sx, sy, tx, ty))
        else:
            if can_capture(b, sx, sy, tx, ty):
                moves.append((sx, sy, tx, ty))
    for sy in range(10):
        for sx in range(9):
            ch = b[sy][sx]
            if ch == EMPTY: continue
            if color == 'r' and not is_red(ch): continue
            if color == 'b' and not is_black(ch): continue
            kind = ch.upper()
            if kind == 'R':
                for d in ((1,0),(-1,0),(0,1),(0,-1)):
                    x, y = sx+d[0], sy+d[1]
                    while 0 <= x < 9 and 0 <= y < 10:
                        if b[y][x] == EMPTY:
                            moves.append((sx,sy,x,y))
                        else:
                            if (is_black(b[y][x]) if color=='r' else is_red(b[y][x])):
                                moves.append((sx,sy,x,y))
                            break
                        x += d[0]; y += d[1]
            elif kind == 'C':
                for d in ((1,0),(-1,0),(0,1),(0,-1)):
                    x, y = sx+d[0], sy+d[1]
                    jumped = False
                    while 0 <= x < 9 and 0 <= y < 10:
                        cell = b[y][x]
                        if cell == EMPTY:
                            if not jumped:
                                moves.append((sx,sy,x,y))
                        else:
                            if jumped:
                                if (is_black(cell) if color=='r' else is_red(cell)):
                                    moves.append((sx,sy,x,y))
                                break
                            jumped = True
                        x += d[0]; y += d[1]
            elif kind == 'H':
                for dx, dy in ((1,2),(2,1),(2,-1),(1,-2),(-1,-2),(-2,-1),(-2,1),(-1,2)):
                    if abs(dx) == 2:
                        lx, ly = sx+dx//2, sy
                    else:
                        lx, ly = sx, sy+dy//2
                    tx, ty = sx+dx, sy+dy
                    if 0 <= tx < 9 and 0 <= ty < 10 and b[ly][lx] == EMPTY:
                        tgt = b[ty][tx]
                        if tgt == EMPTY:
                            moves.append((sx,sy,tx,ty))
                        elif (is_black(tgt) if color=='r' else is_red(tgt)):
                            moves.append((sx,sy,tx,ty))
            elif kind == 'E':
                for dx, dy in ((2,2),(2,-2),(-2,2),(-2,-2)):
                    tx, ty = sx+dx, sy+dy
                    if not (0 <= tx < 9 and 0 <= ty < 10): continue
                    if not own_side(tx, ty, color): continue
                    eye = (sx+dx//2, sy+dy//2)
                    if b[eye[1]][eye[0]] != EMPTY: continue
                    tgt = b[ty][tx]
                    if tgt == EMPTY:
                        moves.append((sx,sy,tx,ty))
                    elif (is_black(tgt) if color=='r' else is_red(tgt)):
                        moves.append((sx,sy,tx,ty))
            elif kind == 'A':
                for dx, dy in ((1,1),(1,-1),(-1,1),(-1,-1)):
                    tx, ty = sx+dx, sy+dy
                    if not in_palace(tx, ty, color): continue
                    tgt = b[ty][tx]
                    if tgt == EMPTY:
                        moves.append((sx,sy,tx,ty))
                    elif (is_black(tgt) if color=='r' else is_red(tgt)):
                        moves.append((sx,sy,tx,ty))
            elif kind == 'K':
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    tx, ty = sx+dx, sy+dy
                    if not in_palace(tx, ty, color): continue
                    tgt = b[ty][tx]
                    if tgt == EMPTY:
                        moves.append((sx,sy,tx,ty))
                    elif (is_black(tgt) if color=='r' else is_red(tgt)):
                        moves.append((sx,sy,tx,ty))
            elif kind == 'P':
                fwd = 1 if color == 'r' else -1
                ty0 = sy + fwd
                if 0 <= ty0 < 10 and b[ty0][sx] == EMPTY:
                    moves.append((sx, sy, sx, ty0))
                elif 0 <= ty0 < 10 and (is_black(b[ty0][sx]) if color=='r' else is_red(b[ty0][sx])):
                    moves.append((sx, sy, sx, ty0))
                if crossed_river(sy, color):
                    for dx in (-1, 1):
                        tx = sx+dx
                        if 0 <= tx < 9:
                            tgt = b[sy][tx]
                            if tgt == EMPTY:
                                moves.append((sx, sy, tx, sy))
                            elif (is_black(tgt) if color=='r' else is_red(tgt)):
                                moves.append((sx, sy, tx, sy))
    return moves

def can_move_to(b, sx, sy, tx, ty):
    """非吃子的移动合法性: 按目标为空格的走法规则"""
    ch = b[sy][sx]
    color = 'r' if is_red(ch) else 'b'
    kind = ch.upper()
    if kind == 'R':
        if sx == tx:
            return onsame_file_no_block(b, sx, sy, ty) and sy != ty
        if sy == ty:
            for x in range(min(sx,tx)+1, max(sx,tx)):
                if b[sy][x] != EMPTY: return False
            return sx != tx
        return False
    if kind == 'C':
        # 非吃: 直线滑动, 路径全空
        if sx == tx and sy != ty:
            return onsame_file_no_block(b, sx, sy, ty)
        if sy == ty and sx != tx:
            for x in range(min(sx,tx)+1, max(sx,tx)):
                if b[sy][x] != EMPTY: return False
            return sx != tx
        return False
    if kind == 'H':
        dx, dy = tx-sx, ty-sy
        if (abs(dx), abs(dy)) not in ((1,2),(2,1)): return False
        leg = (sx+dx//2, sy) if abs(dx)==2 else (sx, sy+dy//2)
        return b[leg[1]][leg[0]] == EMPTY
    if kind == 'E':
        dx, dy = tx-sx, ty-sy
        if (abs(dx), abs(dy)) != (2,2): return False
        if not own_side(tx, ty, color): return False
        eye = (sx+dx//2, sy+dy//2)
        return b[eye[1]][eye[0]] == EMPTY
    if kind == 'A':
        return in_palace(tx, ty, color) and abs(tx-sx)==1 and abs(ty-sy)==1
    if kind == 'K':
        return abs(tx-sx)+abs(ty-sy)==1 and in_palace(tx, ty, color)
    if kind == 'P':
        if color == 'r':
            if ty == sy+1 and tx == sx: return True
            if crossed_river(sy,'r') and ty==sy and abs(tx-sx)==1: return True
        else:
            if ty == sy-1 and tx == sx: return True
            if crossed_river(sy,'b') and ty==sy and abs(tx-sx)==1: return True
    return False

def clone(b):
    return [row[:] for row in b]

def in_check(b, color):
    k = find_king(b, color)
    if not k:
        return False
    if kings_face(b):
        return True  # 对脸即视为被将(局面非法/被将)
    enemy = 'b' if color == 'r' else 'r'
    return len(attackers(b, k[0], k[1], enemy)) > 0

def legal_moves(b, color):
    """完整合法着法: 排除走完被将/对脸的着法"""
    res = []
    for m in pseudo_moves(b, color):
        nb = clone(b)
        nb[m[3]][m[2]] = nb[m[1]][m[0]]
        nb[m[1]][m[0]] = EMPTY
        if not in_check(nb, color):
            res.append(m)
    return res

def apply_move(b, move_str):
    """返回 (ok, board, msg)"""
    try:
        f, t = move_str.strip().lower().split('-')
        sx, tx = ord(f[0])-ord('a'), ord(t[0])-ord('a')
        sy, ty = int(f[1]), int(t[1])
    except Exception:
        return False, b, '走法格式错误, 应为 <列><行>-<列><行>, 例: h2-e2'
    if not (0 <= sx < 9 and 0 <= sy < 10 and 0 <= tx < 9 and 0 <= ty < 10):
        return False, b, f'坐标越界: {move_str}'
    ch = b[sy][sx]
    if ch == EMPTY:
        return False, b, f'起点 {f} 无子'
    color = 'r' if is_red(ch) else 'b'
    tgt = b[ty][tx]
    if tgt != EMPTY:
        same = is_red(tgt) if color=='r' else is_black(tgt)
        if same:
            return False, b, f'终点 {t} 是己方棋子({tgt})'
        if not can_capture(b, sx, sy, tx, ty):
            return False, b, f'非法着法: {move_str} (该子吃不到 {t})'
    else:
        if not can_move_to(b, sx, sy, tx, ty):
            return False, b, f'非法着法: {move_str} (走法不符合该子规则)'
    nb = clone(b)
    nb[ty][tx] = ch
    nb[sy][sx] = EMPTY
    if in_check(nb, color):
        return False, b, f'非法着法: {move_str} (走完己方老将被将/对脸)'
    return True, nb, '合法'

def evaluate_state(b, side_to_move):
    """判定当前局面终局状态. side_to_move 是轮到的一方.
    返回 (status, detail), status: playing / red_win / black_win / draw"""
    lm = legal_moves(b, side_to_move)
    if len(lm) == 0:
        if in_check(b, side_to_move):
            return ('black_win' if side_to_move=='r' else 'red_win'), '将死(无应将)'
        else:
            return ('black_win' if side_to_move=='r' else 'red_win'), '困毙(无子可动)'
    # 子力不足: 双方都无攻击子力(车马炮兵卒) -> 和（残局可被判和兜底）
    def has_attack(b, color):
        pc = set('RHCP') if color == 'r' else set('rhcp')
        for y in range(10):
            for x in range(9):
                ch = b[y][x]
                if ch in pc:
                    return True
        return False
    if not has_attack(b, 'r') and not has_attack(b, 'b'):
        return 'draw', '双方子力不足(无法取胜)'
    return 'playing', ''

def ascii_board(b):
    lines = []
    files = '  a b c d e f g h i'
    lines.append('  ┌───┬───┬───┬───┬───┬───┬───┬───┬───┐')
    for y in range(9, -1, -1):
        row = []
        for x in range(9):
            row.append(' ' + (b[y][x] if b[y][x] != EMPTY else '·') + ' ')
        if y == 9:
            lines.append('9│' + '│'.join(row) + f'│ 黑方(将)')
        elif y == 0:
            lines.append('0│' + '│'.join(row) + f'│ 红方(帅)')
        else:
            lines.append(str(y) + '│' + '│'.join(row) + '│')
        if y > 0:
            lines.append('  ├───┼───┼───┼───┼───┼───┼───┼───┼───┤')
    lines.append('  └───┴───┴───┴───┴───┴───┴───┴───┴───┘')
    lines.append(files)
    return '\n'.join(lines)

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    b = make_board()
    if cmd == 'init':
        pass
    elif cmd == 'fen':
        b = board_from_fen(sys.argv[2])
    elif cmd == 'move':
        b = board_from_fen(sys.argv[2]) if len(sys.argv) > 3 else b
        ok, b, msg = apply_move(b, sys.argv[2] if len(sys.argv) <= 3 else sys.argv[3])
        print(f'校验: {msg}' + (f' | {sys.argv[3] if len(sys.argv)>3 else ""}' if ok else ''))
        if not ok:
            print(ascii_board(b))
            print(f'FEN: {board_to_fen(b)}')
            return 1
    elif cmd == 'legal':
        b = board_from_fen(sys.argv[3]) if len(sys.argv) > 3 else b
        color = sys.argv[2]
        ms = legal_moves(b, color)
        print(f'{color} 方合法着法 {len(ms)} 个:')
        for sx, sy, tx, ty in ms:
            print(f'  {chr(97+sx)}{sy}-{chr(97+tx)}{ty}')
        return 0
    elif cmd == 'resign':
        color = sys.argv[2]
        print(f'win={ "black_win" if color=="r" else "red_win" }')
        return 0
    else:
        print('未知命令', file=sys.stderr)
        return 2

    # 初始局面的终局状态与打印
    st, _ = evaluate_state(b, 'r')
    print(ascii_board(b))
    print()
    print('FEN:', board_to_fen(b))
    print('状态:', st)
    print('红方合法着法数:', len(legal_moves(b, 'r')))
    print('黑方合法着法数:', len(legal_moves(b, 'b')))
    return 0

if __name__ == '__main__':
    sys.exit(main())