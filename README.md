# Xiangqi Model Match — AI 象棋自动对战框架

让两个 LLM 在一台机器上**全自动**下中国象棋，由**机械规则引擎**逐手校验，绝无人工裁决、绝无 LLM 互信。纯标准库，零第三方依赖。

## 特性

- **三种选手类型**：`openai`（OpenAI 兼容 `/v1/chat/completions`）、`ollama`（Ollama `/api/chat`）、`stdio`（终端逐步输入，适合人 vs 模型演示）
- **低速模型适配**：单步超时可配置（默认 `openai=600s / ollama=300s`），容纳 27B 等大模型单步 85–110s+ 的推理耗时
- **机械裁判**：每步过 `referee.py` 规则引擎，非法走子回灌完整合法着法列表让模型纠正
- **自动终局**：将死 / 困毙 / 三次重复 / 自然限着（120 手无吃子）/ 子力不足 / 认输 / 犯规判负
- **系列赛**：支持 BO_N 赛制（三局两胜等），每局交换先后手，先达 `ceil(N/2)` 局夺冠
- **断点续跑**：状态落 `match_state.json`，日志落 `arena_log.md`
- **内部重试**：模型调用 3 次内部重试吸收瞬时抖动，全部失败才按犯规处理

## 快速开始

```bash
git clone https://github.com/JohnYang060424/xiangqi-model-match.git
cd xiangqi-model-match

# 两个模型自动对战（三局两胜）
python scripts/arena.py init \
  --red  "openai|http://127.0.0.1:8000/v1|sk-local|qwen3.8-27b|600" \
  --black "ollama|http://127.0.0.1:11435|api-key|qwen3:30b-a3b|300" \
  --best-of 3 --match-id "a3b-vs-27B"

python scripts/arena.py run          # 全自动跑完
python scripts/arena.py status       # 查看当前局面与比分
```

## 选手格式

用 `|` 分隔（避免与 URL 中的 `:` 冲突）：

| 类型 | 格式 | 说明 |
|---|---|---|
| `openai` | `openai\|<base_url>\|<api_key>\|<model>\|<timeout>` | OpenAI 兼容 `/v1/chat/completions`（vLLM / llama-server 等） |
| `ollama` | `ollama\|<base_url>\|<api_key>\|<model>\|<timeout>` | Ollama `/api/chat`，原生支持 system + `think:False` |
| `stdio` | `stdio` | 终端逐步输入，适合人 / agent 演示 |

- `<timeout>` 为单步模型调用超时（秒），**可选**，未给则按类型取默认值（`openai=600 / ollama=300`）
- `<api_key>` 无鉴权时留空即可

### 人 vs 模型（逐步模式）

```bash
python scripts/arena.py init --red stdio --black "openai|http://127.0.0.1:8000/v1||qwen3.8-27b"
python scripts/arena.py step              # 打印当前局面 + 你方全部合法着法
python scripts/arena.py step h2-e2         # 提交走法，自动走对方一手
```

## 命令参考

| 命令 | 说明 |
|---|---|
| `arena.py init --red <spec> --black <spec> [--best-of 3] [--match-id "..."]` | 初始化系列赛，抛硬币决定 G1 先后手 |
| `arena.py run [--max-plies N]` | 全自动跑（含 stdio 选手时退化为逐步并提示） |
| `arena.py step [<from>-<to>]` | 逐步：不给走法则打印局面，给了则落子并自动走对方一手 |
| `arena.py status` | 查看当前局面与比分 |

## 坐标与记谱

- 列 `a`~`i`（左→右），行 `0`~`9`（下→上，红底线=0，黑底线=9）——始终以红方视角
- 走子格式：`<from>-<to>`，如 `h2-e2`（炮二平五）、`b9-c7`（马 8 进 7）
- 棋子代号：红 `R车 H马 E相 A仕 K帅 C炮 P兵` / 黑 `r车 h马 e象 a士 k将 c炮 p卒`

## 终局判定

| 类型 | 规则 |
|---|---|
| 将死 | 被将军且无合法应将 → 负 |
| 困毙 | 无合法走子且未被将军 → 负（中国象棋规则，与国际象棋不同） |
| 三次重复 | 同一局面（含轮到方）出现 3 次 → 和 |
| 自然限着 | 连续 120 手无吃子 → 和 |
| 子力不足 | 双方均无攻击子力（车马炮兵卒）→ 和 |
| 认输 | 模型输出 `RESIGN` / `认输` / `投降` → 负 |
| 犯规判负 | 单局连续 3 次非法走子（含空输出）→ 负 |

## 架构

```
┌─────────────┐   走子提议    ┌──────────────┐
│  红方选手    │ ───────────▶ │              │
│ (openai/    │              │  arena.py    │ ── 调用 ──▶ referee.py
│  ollama/    │              │  (runner)    │     (规则引擎)
│  stdio)     │              │              │ ◀── 校验结果 ──
└─────────────┘              └──────────────┘
┌─────────────┐   走子提议             │
│  黑方选手    │ ────────────────────▶ │
└─────────────┘                        │
                                       ▼
                              match_state.json  (状态)
                              arena_log.md      (日志)
```

`arena.py` 是 runner：初始化系列赛 → 循环推进每手 → 调用 `referee.py` 校验合法性 → 合法则落子记录、非法则回灌合法着法列表让模型纠正 → 判定终局 → 记比分 → 开下一局或结束。

## 文件

| 文件 | 说明 |
|---|---|
| `scripts/arena.py` | 自动对战 runner（openai/ollama/stdio 选手、BO_N 赛制、犯规判负、断点续跑，纯标准库 ~620 行） |
| `scripts/referee.py` | 中国象棋规则引擎（走法生成 / 合法性 / 将军 / 将死 / 困毙 / 和棋 / FEN / ASCII 棋盘，纯标准库 ~500 行） |

## 依赖

- Python 3.8+（仅标准库）
- 两个 LLM 服务端（OpenAI 兼容或 Ollama），或一个模型 + 一个人

## License

MIT
