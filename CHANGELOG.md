# Changelog

本文件记录 xiangqi-model-match 的版本变更。版本号遵循「主.次.修」：
大版本=架构级重写，次版本（+0.1）=新特性/行为修正，修订号=纯 bugfix。

## v1.1.0（2026-08-21）

27B 自我博弈实测（海光 BW3000 单卡，vllm-dcu 后端）暴露的两个行为缺陷修复 + 思考模式可控。

### 修复

- **犯规计数恢复「连续」语义**：此前 `faults` 计数只增不清零，实际行为是"累计 3 次非法走子判负"，与 README 声称的"连续 3 次"不符——偶尔一次非法（被纠错后继续正常下）也会累积，实测 3 局均在约 10 手时被误判终局。现在走子成功后清零该方计数。
- **openai 选手默认开启思考链**：vLLM 后端会**严格执行** `enable_thinking=False`（transformers 自建服务则忽略该参数），关闭思考导致 27B 棋力骤降（红黑坐标混淆、来回穿梭成和）。默认改为 `enable_thinking=True`，且 `max_tokens` 1024 → 4096（预算太小会被截断在思考中途导致解析失败）。

### 新增

- **思考模式可按选手配置**：选手格式新增可选第 6 段 `think=on|off`（默认 `openai=on / ollama=off`），支持混合对照实验（如开思考 vs 关思考 A/B）。
- `arena.py version` 命令与 `__version__` 常量。
- 本 CHANGELOG.md；README 新增「思考模式与后端兼容」小节（vLLM / transformers / Ollama 三种后端行为对照表）。

## v1.0.0（2026-08-20）

首个重构版：`arena.py` 自动 runner 替换旧文件式中继范式。

- 三种选手类型（openai / ollama / stdio），BO_N 系列赛制（每局交换先后手）
- 机械裁判 `referee.py`（走法生成 / 将死 / 困毙 / 三次重复 / 自然限着 / 子力不足 / 认输 / 犯规判负）
- 断点续跑（`match_state.json` + `arena_log.md`），模型调用内部 3 次重试
- 纯标准库，零第三方依赖
