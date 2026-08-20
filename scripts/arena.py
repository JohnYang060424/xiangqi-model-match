#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arena.py — 中国象棋 AI 自动对战 runner（三局两胜 / 任意 BO_N 赛制）

设计目标
--------
让两个 LLM 在一台机器上**自动**对弈，裁判由 referee.py 机械规则引擎担任，
绝无 LLM 互信、绝无人工裁决。支持两类选手：

  openai  调用任意 OpenAI 兼容 /v1/chat/completions 接口（base_url / api_key / model）
  stdio   由人在终端（或本对话里的 agent）逐步给出走法，适合「人 vs 模型」或演示

特性
----
  * 机械裁判：每步过 referee.apply_move，非法走子回灌合法着法列表让模型纠正
  * 3 次犯规（连续无法给出合法走子）判负；支持认输（输出 RESIGN / 认输）
  * 自动终局：将死 / 困毙 / 三次重复 / 自然限着(120手无吃子) / 子力不足 / 认输 / 犯规
  * 三局两胜：每局交换先后手（G1 抛硬币，G2 交换，G3 重抛），先达 ceil(N/2) 胜者夺冠
  * 断点续跑：状态落 match_state.json；日志落 arena_log.md（纯标准库，无第三方依赖）

用法
----
  # 初始化（两模型自动对战）。选手格式:
  #   openai|<base_url>|<api_key>|<model>[|<timeout秒>][|<think>]
  #   ollama|<base_url>|<api_key>|<model>[|<timeout秒>][|<think>]   # Ollama /api/chat（如 a3b）
  #   stdio
  # timeout 可选，适配低速模型（默认 openai=600s / ollama=300s）。
  # think 可选（on/off）：是否让模型带思考链下棋。默认 openai=on（大模型棋力依赖
  # 思考链，max_tokens=4096 即为容纳思考而设）、ollama=off（原生 think 开关，速度快）。
  # 注意：vLLM 严格执行 enable_thinking，transformers 后端则忽略该参数——详见 README。
  python arena.py init --red "ollama|http://127.0.0.1:11435|api-key|qwen3:30b-a3b|300|off" \
                       --black "openai|http://127.0.0.1:8000/v1|sk-local|qwen3.8-27b|600|on" \
                       --best-of 3 --match-id "a3b-vs-27B"

  # 全自动跑完（含 stdio 选手时会自动退化为逐步模式）
  python arena.py run [--max-plies N]

  # 逐步模式（任一选手为 stdio 时）：每条命令推进「我方一手 + 对方一手」
  python arena.py step [<from>-<to>]     # 不给 move 时打印当前局面，等你输入

  # 查看当前局面
  python arena.py status

  # 查看版本
  python arena.py version
"""
import sys, os, json, re, time, random, urllib.request, urllib.error

# 让脚本无论从哪个目录运行都能 import 同目录的 referee
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import referee as R

__version__ = '1.1.0'

STATE = 'match_state.json'
LOG = 'arena_log.md'
FAULT_LIMIT = 3          # 单局连续犯规上限，达到即判负
NATURAL_LIMIT = 120      # 连续无吃子手数（自然限着）-> 和棋

# ---------------------------------------------------------------------------
# 选手配置解析
# ---------------------------------------------------------------------------
def parse_player_spec(spec):
    """选手格式（用 | 分隔，避免与 URL 中的 : 冲突）：
      stdio
      openai|<base>|<key>|<model>[|<timeout>][|<think>]
      ollama|<base>|<key>|<model>[|<timeout>][|<think>]
    timeout（秒）为单步模型调用超时，用于适配低速模型（如 27B 单步可达数百秒）。
    未显式给出时按类型取默认：openai=600，ollama=300。
    think（on/off）为是否带思考链下棋：默认 openai=on、ollama=off。
    背景：vLLM 后端严格执行 enable_thinking（关掉会显著削弱大模型棋力），
    transformers 后端则忽略该参数；Ollama 原生 think 开关默认关闭以提速。
    """
    if spec == 'stdio' or spec.startswith('stdio'):
        return {'type': 'stdio'}
    parts = spec.split('|')
    ptype = parts[0]
    if ptype not in ('openai', 'ollama'):
        raise ValueError('未知选手类型: ' + spec)
    base = parts[1] if len(parts) > 1 else ''
    key = parts[2] if len(parts) > 2 else ''
    model = parts[3] if len(parts) > 3 else ''
    timeout = float(parts[4]) if len(parts) > 4 and parts[4] else 0.0
    # 第 6 段可选 think 开关：on/true/1/yes -> True，off/false/0/no -> False
    think_raw = parts[5].strip().lower() if len(parts) > 5 and parts[5].strip() else ''
    if think_raw in ('on', 'true', '1', 'yes'):
        think = True
    elif think_raw in ('off', 'false', '0', 'no'):
        think = False
    else:
        think = (ptype == 'openai')   # 默认：openai 开思考（棋力依赖），ollama 关思考（提速）
    p = {'type': ptype, 'base': base, 'key': key, 'model': model, 'think': think}
    if ptype == 'openai':
        p['timeout'] = timeout if timeout else 600.0
    else:  # ollama
        p['timeout'] = timeout if timeout else 300.0
        p['num_ctx'] = 4096
    return p


def call_openai(p, prompt, timeout=300):
    # 不发 system 消息：部分推理服务（如本仓库实测的 Qwen3.8 服务端）只在「无 system」
    # 时注入「直接简洁、不要思考」的压制 system；若我们自带 system 反而阻断它，
    # 模型会自由长思考拖慢对战。所有走子指令已包含在 user 提示里。
    # 思考模式：vLLM 会严格执行 enable_thinking；transformers 后端则忽略此参数。
    # 27B 实测：关思考后棋力骤降（红黑坐标混淆/来回穿梭成和），开思考则正常攻防；
    # max_tokens=4096 即为容纳思考链而设（预算太小会被截断在思考中途导致解析失败）。
    # 故 openai 选手默认 think=on；关闭思考时输出预算降回 1024（无思考链无需 4096）。
    think = p.get('think', True)
    data = {
        "model": p['model'],
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096 if think else 1024,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    headers = {"Content-Type": "application/json"}
    if p.get('key'):
        headers["Authorization"] = "Bearer " + p['key']
        headers["x-api-key"] = p['key']
    url = p['base'].rstrip('/') + '/chat/completions'
    body = json.dumps(data).encode()
    last_err = None
    # 内部重试：27B 服务端偶发抖动（连接重置/瞬时 5xx）会导致单次调用抛异常，
    # 若直接抛出会中断整条 step 并破坏断点续跑；这里先原地重试，全部失败再上抛，
    # 由 play_one_move 的 openai 分支按「犯规」逻辑统一处理。
    for _attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            return resp["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


SYS_PROMPT = (
    "你是一名严谨的中国象棋选手，只负责想一步合法走子，其余规则由机械裁判校验。"
    "不要解释，不要闲聊，不要加棋子字母前缀。"
)


def call_ollama(p, prompt, timeout=300):
    """调用 Ollama /api/chat（如 a3b=qwen3:30b-a3b）。
    Ollama 原生支持 system 消息与 think 开关（默认关思考以提速，
    可在选手格式第 6 段给 on 打开）；超时按低速模型配置。
    """
    data = {
        "model": p['model'],
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": bool(p.get('think', False)),
        "options": {"num_ctx": p.get('num_ctx', 4096), "temperature": 0.3},
    }
    headers = {"Content-Type": "application/json"}
    if p.get('key'):
        headers["x-api-key"] = p['key']
    url = p['base'].rstrip('/') + '/api/chat'
    body = json.dumps(data).encode()
    last_err = None
    # 内部重试：Ollama 偶发连接抖动（空 context / 瞬时 5xx）会导致单次调用抛异常，
    # 若直接抛出会中断整条 step 并破坏断点续跑；这里先原地重试，全部失败再上抛，
    # 由 play_one_move 的 openai/ollama 分支按「犯规」逻辑统一处理。
    for _attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            return resp["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err

# ---------------------------------------------------------------------------
# 走子解析与提示构造
# ---------------------------------------------------------------------------
def parse_move(text):
    """从模型原始输出里抽取 from-to 坐标（兼容思考标签与棋子字母前缀）。"""
    if '</think:6124c78e>' in text:
        text = text.split('</think:6124c78e>')[-1]
    ms = re.findall(r'[A-Za-z]?([a-i][0-9]-[a-i][0-9])', text)
    return ms[-1] if ms else None


def detect_resign(text):
    t = text.lower()
    return ('resign' in t) or ('认输' in text) or ('投降' in text) or ('我认输' in text)


def build_prompt(board, side, n, retry=None):
    """retry = (bad_move, reason, legal_moves_str) 时，作为纠正提示。"""
    own, enemy = [], []
    for y in range(10):
        for x in range(9):
            ch = board[y][x]
            if ch == R.EMPTY:
                continue
            tag = f"{ch}{chr(97 + x)}{y}"
            if R.is_red(ch):
                (own if side == 'r' else enemy).append(tag)
            else:
                (own if side == 'b' else enemy).append(tag)
    me = '红' if side == 'r' else '黑'
    p = (
        f"【第{n}手】轮到你走棋，你执{me}方（{me}先行）。\n"
        f"当前棋盘（红方视角；a-i 左→右，0-9 下→上；红底线0，黑底线9；·为空）：\n"
        f"{R.ascii_board(board)}\n"
        f"你的{me}方棋子：{' '.join(own)}\n"
        f"对方棋子：{' '.join(enemy)}\n"
        f"要求：只输出一步合法走子，格式严格为 from-to（例如 h2-e2）。列用 a-i、行用 0-9。\n"
        f"规则：不可走到己方棋子格；注意蹩马腿、塞象眼、炮需恰好一个炮架才能吃子、"
        f"将帅不可直接对脸、仕/帅限九宫、相/象不过河。\n"
        f"只输出 from-to 这 5 个字符，不要解释、不要引号、不要加棋子字母前缀。"
    )
    if retry:
        bad, reason, legal_str = retry
        p += (
            f"\n你上一步 '{bad}' 被裁判判为非法：{reason}。"
            f"请改走一步合法着法，仍只输出 from-to。\n"
            f"你方当前全部合法着法：{legal_str}"
        )
    return p


# ---------------------------------------------------------------------------
# 状态管理
# ---------------------------------------------------------------------------
def load_state():
    with open(STATE, encoding='utf-8') as f:
        st = json.load(f)
    # JSON 把 int 键序列化成 str，这里把 faults 的键归一回 int（其余 FEN 类键本就是 str）
    if isinstance(st.get('current'), dict) and isinstance(st['current'].get('faults'), dict):
        st['current']['faults'] = {int(k): v for k, v in st['current']['faults'].items()}
    return st


def save_state(st):
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def new_game(st, game_no, red_idx):
    """开新的一局。red_idx = 执红的选手索引(0/1)。"""
    st['current'] = {
        'game_no': game_no,
        'red_idx': red_idx,
        'black_idx': 1 - red_idx,
        'fen': R.board_to_fen(R.make_board()),
        'side': 'r',
        'ply': 0,
        'history': [],         # {num, side, move, piece, capture}
        'faults': {0: 0, 1: 0},
        'no_capture_ply': 0,
        'fen_count': {},
        'result': None,
        'reason': '',
        'start': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_state(st)


def player_of(st, side):
    cur = st['current']
    idx = cur['red_idx'] if side == 'r' else cur['black_idx']
    return st['players'][idx], idx


def legal_moves_str(board, side):
    ms = R.legal_moves(board, side)
    return ', '.join(f"{chr(97 + sx)}{sy}-{chr(97 + tx)}{ty}" for sx, sy, tx, ty in ms)


# ---------------------------------------------------------------------------
# 单步推进（核心）
# ---------------------------------------------------------------------------
def play_one_move(st, forced_move=None):
    """
    推进一手。返回 (status, info)：
      status in {'need_input', 'game_over', 'match_over'}
      - need_input: 轮到 stdio 选手且未提供 forced_move，等待下次 step <move>
      - game_over: 本局结束（已记比分），需开下一局或已分胜负
      - match_over: 整个系列赛结束
    """
    cur = st['current']
    if cur['result']:
        return ('game_over', {'reason': cur['result']})

    board = R.board_from_fen(cur['fen'])
    side = cur['side']
    n = cur['ply'] + 1
    player, pidx = player_of(st, side)
    me = '红' if side == 'r' else '黑'

    # ---- 取走法 ----
    if player['type'] == 'stdio':
        if forced_move is None:
            # 等待外部输入：打印局面 + 合法着法
            return ('need_input', {
                'side': side, 'ply': n, 'board': R.ascii_board(board),
                'legal': legal_moves_str(board, side),
                'player_idx': pidx,
            })
        raw = forced_move
        mv = parse_move(forced_move)
    else:
        # openai / ollama：调接口，非法时回灌合法着法重试（最多 FAULT_LIMIT 次）
        # 低速模型（如 27B）通过 player['timeout'] 放宽单步超时，避免生成长链思考时被截断。
        retry = None
        raw = None
        for attempt in range(FAULT_LIMIT):
            prompt = build_prompt(board, side, n, retry)
            try:
                if player['type'] == 'ollama':
                    raw = call_ollama(player, prompt, player.get('timeout', 300))
                else:
                    raw = call_openai(player, prompt, player.get('timeout', 600))
            except Exception as e:
                raw = ''
                reason = f'调用模型异常: {e}'
            else:
                reason = None
            if detect_resign(raw or ''):
                mv = '__RESIGN__'
                break
            mv = parse_move(raw or '')
            if mv is None:
                reason = '未解析到 from-to 坐标'
            else:
                ok, _, msg = R.apply_move(board, mv)
                if ok:
                    break
                reason = msg
            # 非法 -> 累计犯规，回灌合法着法重试
            cur['faults'][pidx] += 1
            if cur['faults'][pidx] >= FAULT_LIMIT:
                return _forfeit(st, pidx, f'连续{FAULT_LIMIT}次非法走子(最后:{mv})')
            retry = (mv or '(空)', reason, legal_moves_str(board, side))
        if mv == '__RESIGN__':
            return _forfeit(st, pidx, '认输')

    # ---- stdio 选手的校验（forced_move 路径）----
    if player['type'] == 'stdio':
        if detect_resign(forced_move or ''):
            return _forfeit(st, pidx, '认输')
        mv = parse_move(forced_move)
        if mv is None:
            cur['faults'][pidx] += 1
            save_state(st)
            if cur['faults'][pidx] >= FAULT_LIMIT:
                return _forfeit(st, pidx, f'连续{FAULT_LIMIT}次未给出合法坐标')
            return ('need_input', {
                'side': side, 'ply': n, 'board': R.ascii_board(board),
                'legal': legal_moves_str(board, side),
                'player_idx': pidx, 'error': f'无法解析走法: {forced_move}（犯规 {cur["faults"][pidx]}/{FAULT_LIMIT}）',
            })
        ok, nb, msg = R.apply_move(board, mv)
        if not ok:
            cur['faults'][pidx] += 1
            save_state(st)
            if cur['faults'][pidx] >= FAULT_LIMIT:
                return _forfeit(st, pidx, f'连续{FAULT_LIMIT}次非法走子(最后:{mv}:{msg})')
            return ('need_input', {
                'side': side, 'ply': n, 'board': R.ascii_board(board),
                'legal': legal_moves_str(board, side),
                'player_idx': pidx, 'error': f'非法: {msg}（犯规 {cur["faults"][pidx]}/{FAULT_LIMIT}）',
            })

    # ---- 落子 ----
    sx, sy = ord(mv[0]) - ord('a'), int(mv[1])
    tx, ty = ord(mv[3]) - ord('a'), int(mv[4])
    piece = board[sy][sx]
    captured = board[ty][tx] != R.EMPTY
    nb = R.clone(board)
    nb[ty][tx] = piece
    nb[sy][sx] = R.EMPTY

    cur['ply'] += 1
    # 走子成功：清零该方犯规计数（语义为"连续"犯规判负，而非累计）
    cur['faults'][pidx] = 0
    cur['history'].append({
        'num': cur['ply'], 'side': me, 'move': mv,
        'piece': piece, 'capture': captured,
    })
    cur['fen'] = R.board_to_fen(nb)
    cur['no_capture_ply'] = 0 if captured else cur['no_capture_ply'] + 1

    # 三次重复判定
    kf = R.board_to_fen(nb) + ('r' if side == 'r' else 'b')  # 走子后轮到对方
    cur['fen_count'][kf] = cur['fen_count'].get(kf, 0) + 1

    nxt = 'b' if side == 'r' else 'r'
    stt, detail = R.evaluate_state(nb, nxt)
    if stt == 'playing':
        if cur['fen_count'][kf] >= 3:
            stt, detail = 'draw', f'三次重复局面（第{cur["ply"]}手）'
        elif cur['no_capture_ply'] >= NATURAL_LIMIT:
            stt, detail = 'draw', f'自然限着：连续{cur["no_capture_ply"]}手无吃子（第{cur["ply"]}手）'

    if stt != 'playing':
        cur['result'] = stt
        cur['reason'] = detail + f'（共{cur["ply"]}手）'
        _record_game_result(st, stt, detail)
        save_state(st)
        _append_log_move(st, me, mv, piece, captured, stt, detail)
        return _after_game(st)

    cur['side'] = nxt
    save_state(st)
    _append_log_move(st, me, mv, piece, captured)
    # 继续推进：若下一手是对方(openai)则自动；若是 stdio 则停下等输入
    nxt_player, _ = player_of(st, nxt)
    if nxt_player['type'] == 'stdio':
        return ('need_input', {
            'side': nxt, 'ply': cur['ply'] + 1, 'board': R.ascii_board(nb),
            'legal': legal_moves_str(nb, nxt), 'player_idx': st['current']['black_idx'] if nxt == 'b' else st['current']['red_idx'],
        })
    # 下一手是 openai：递归自动走（最多再走一手，避免深层递归由外层循环控制）
    return play_one_move(st)


def _forfeit(st, pidx, reason):
    cur = st['current']
    loser_side = 'r' if cur['red_idx'] == pidx else 'b'
    winner = 'black_win' if loser_side == 'r' else 'red_win'
    cur['result'] = winner
    cur['reason'] = f'{reason}（判负）'
    _record_game_result(st, winner, cur['reason'])
    save_state(st)
    _append_log_move(st, '—', '—', '—', False, winner, cur['reason'])
    return _after_game(st)


def _record_game_result(st, stt, detail):
    cur = st['current']
    red_name = st['players'][cur['red_idx']]['name']
    black_name = st['players'][cur['black_idx']]['name']
    if stt == 'red_win':
        st['score']['r'] += 1
        winner_name = red_name
    elif stt == 'black_win':
        st['score']['b'] += 1
        winner_name = black_name
    else:
        winner_name = '和棋'
    st['games'].append({
        'no': cur['game_no'], 'red': red_name, 'black': black_name,
        'result': stt, 'reason': detail, 'winner': winner_name,
        'plies': cur['ply'],
    })


def _after_game(st):
    target = (st['best_of'] // 2) + 1
    if st['score']['r'] >= target or st['score']['b'] >= target or \
       (len(st['games']) >= st['best_of']):
        return ('match_over', {'score': st['score'], 'games': st['games']})
    # 开下一局：换先（G1 抛硬币；之后交替；如需 G3 再抛）
    next_no = len(st['games']) + 1
    if next_no == 2:
        red_idx = 1 - st['games'][0].get('red_idx', st['current']['red_idx'])
    else:
        red_idx = random.randint(0, 1)
    # 记录刚结束的局号：new_game 会用 next_no 覆盖 current['game_no']，
    # 若不先存下来，cmd_step 终局提示会错位一（把"下一局"当成"刚结束的局"）。
    ended_no = st['current']['game_no']
    new_game(st, next_no, red_idx)
    return ('game_over', {'ended_no': ended_no, 'next_red_idx': red_idx, 'score': st['score']})


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def _append_log_move(st, me, mv, piece, captured, stt=None, detail=None):
    cur = st['current']
    line = f"| G{cur['game_no']} | {me} | {mv} | {'✅' if mv != '—' else '—'} | {('吃子' if captured else '')} "
    if stt:
        line += f"| **{stt}** {detail}"
    else:
        line += "|"
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def init_log(st):
    with open(LOG, 'w', encoding='utf-8') as f:
        f.write(f"# 🤖♟️ 自动对战日志 · {st['match_id']}\n\n")
        f.write(f"- 赛制：三局两胜（先胜 {st['best_of'] // 2 + 1} 局夺冠）\n")
        f.write(f"- 红方选手：{st['players'][0]['name']} / 黑方选手：{st['players'][1]['name']}\n")
        f.write(f"- 开始：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 📊 比分\n\n")
        f.write("| 局次 | 红方 | 黑方 | 结果 |\n|---|---|---|---|\n")
        for g in st['games']:
            f.write(f"| G{g['no']} | {g['red']} | {g['black']} | {g['winner']} ({g['result']}) |\n")
        f.write(f"\n**总比分：红 {st['score']['r']} - {st['score']['b']} 黑**\n\n")
        f.write("## 📜 走子记录\n\n")
        f.write("| 局 | 执方 | 走子 | 校验 | 备注 |\n|---|---|---|---|---|\n")


def refresh_log_header(st):
    """每局结束后刷新比分表（简单重写头部）。"""
    # 头部已在 init_log 写死，这里只追加终局小结即可，避免复杂重写。
    pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_init(args):
    red_spec = None
    black_spec = None
    best_of = 3
    match_id = 'AI对战'
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--red':
            red_spec = args[i + 1]; i += 2
        elif a == '--black':
            black_spec = args[i + 1]; i += 2
        elif a == '--best-of':
            best_of = int(args[i + 1]); i += 2
        elif a == '--match-id':
            match_id = args[i + 1]; i += 2
        else:
            i += 1
    if not red_spec or not black_spec:
        print('用法: arena.py init --red <spec> --black <spec> [--best-of 3] [--match-id "..."]')
        return 2
    pr = parse_player_spec(red_spec)
    pb = parse_player_spec(black_spec)
    pr['name'] = 'WorkBuddy(stdio)' if pr['type'] == 'stdio' else (pr.get('model') or 'openai')
    pb['name'] = 'WorkBuddy(stdio)' if pb['type'] == 'stdio' else (pb.get('model') or 'openai')
    st = {
        'match_id': match_id, 'best_of': best_of,
        'score': {'r': 0, 'b': 0}, 'games': [],
        'players': [pr, pb], 'current': None,
    }
    red_idx = random.randint(0, 1)
    new_game(st, 1, red_idx)
    save_state(st)
    init_log(st)
    print(f"已初始化对战：{pr['name']}(红?={red_idx == 0}) vs {pb['name']}")
    print(f"G1 抛硬币结果：{'玩家A(索引0)' if red_idx == 0 else '玩家B(索引1)'} 执红")
    print(f"状态文件：{STATE}　日志：{LOG}")
    return 0


def cmd_status():
    st = load_state()
    cur = st['current']
    board = R.board_from_fen(cur['fen'])
    print(R.ascii_board(board))
    print(f"FEN: {cur['fen']}")
    print(f"当前局：G{cur['game_no']}　轮到：{'红' if cur['side'] == 'r' else '黑'}")
    print(f"比分：红 {st['score']['r']} - {st['score']['b']} 黑")
    print(f"本局结果：{cur['result']} {cur['reason']}")


def cmd_step(args):
    st = load_state()
    forced = args[0] if args else None
    status, info = play_one_move(st, forced)
    if status == 'need_input':
        print("⏳ 等待你（stdio 选手）输入走法。当前局面：")
        print(info['board'])
        print(f"轮到：{'红' if info['side'] == 'r' else '黑'}　第{info['ply']}手")
        if info.get('error'):
            print("⚠️ " + info['error'])
        print("你方合法着法：")
        print(info['legal'])
        print(f"\n请输入：python arena.py step <from>-<to>")
    elif status == 'game_over':
        if 'next_red_idx' in info:
            # 用 _after_game 返回的 ended_no（已被 new_game 推进前的真实结束局号），
            # 避免终局提示错位一。
            ended = info.get('ended_no', st['current']['game_no'])
            print(f"🏁 G{ended} 结束。比分 红 {info['score']['r']} - {info['score']['b']} 黑")
            print(f"下一局 G{ended + 1}：玩家{'A' if info['next_red_idx'] == 0 else 'B'} 执红")
        else:
            print(f"本局结束：{info['reason']}")
    elif status == 'match_over':
        print("🏆 系列赛结束！")
        print(f"总比分：红 {info['score']['r']} - {info['score']['b']} 黑")
        for g in info['games']:
            print(f"  G{g['no']}: {g['winner']} — {g['reason']}")
    return 0


def cmd_run(args):
    """全自动跑（含 stdio 时退化为逐步：遇到 need_input 即停并提示）。"""
    max_plies = None
    for i, a in enumerate(args):
        if a == '--max-plies':
            max_plies = int(args[i + 1])
    st = load_state()
    while True:
        if max_plies is not None and st['current']['ply'] >= max_plies:
            print(f"已达 --max-plies {max_plies}，停止。")
            break
        status, info = play_one_move(st)
        if status == 'need_input':
            print("⏸ 系列赛含 stdio 选手，自动退化为逐步模式。请运行：")
            print(f"  python arena.py step <from>-<to>")
            print(f"（当前轮到：{'红' if info['side'] == 'r' else '黑'}）")
            break
        if status == 'match_over':
            print("🏆 系列赛结束！")
            print(f"总比分：红 {info['score']['r']} - {info['score']['b']} 黑")
            for g in info['games']:
                print(f"  G{g['no']}: {g['winner']} — {g['reason']}")
            break
        # game_over 后 _after_game 已开新局，继续循环
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == 'version':
        print('xiangqi-model-match v' + __version__)
        return 0
    elif cmd == 'init':
        return cmd_init(sys.argv[2:])
    elif cmd == 'status':
        cmd_status()
        return 0
    elif cmd == 'step':
        return cmd_step(sys.argv[2:])
    elif cmd == 'run':
        return cmd_run(sys.argv[2:])
    else:
        print('未知命令:', cmd)
        print(__doc__)
        return 2


if __name__ == '__main__':
    sys.exit(main())
