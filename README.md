# agentank-framework

面向 [AgenTank](https://agentank.ai) 的**行为树坦克框架骨架**（开源参考实现）。

> **这是什么**：可构建、可发布、可分析的架构脚手架——根优先级、技能插槽、承诺机制、BT 日志、Terser 压缩与工具链。  
> **这不是什么**：竞赛成品。各大节点 / 技能插槽只有工厂签名与 `TODO`/`FAILURE` 占位，**不保证能打**。策略叶子需自行填写。

## 快速开始

```bash
cd agentank-framework
cp .env.example .env          # 填写 AGENTANK_KEY_*
npm install                   # 可选：安装 terser 供发布压缩
node build-new.js --all-skills
python publish-new.py --only demo-teleport --dry-run   # 验压缩体积，不上传
```

构建产物：`dist/bt-tank-{teleport|shield|boost|overload|stun|freeze|cloak|poison}.js`

## 依赖

| 用途 | 要求 |
|------|------|
| 构建 | Node.js 18+（推荐 22+） |
| 发布 / 对战 / 分析 | Python 3 |
| 发布压缩 | [terser](https://github.com/terser/terser)（`npm i` 或全局安装） |
| 本地模拟对战 | **不内置**，见下方模拟器 |

### 本地模拟器（外链）

本地回归请使用独立仓库（不消耗平台次数、可跑未发布代码）：

**https://github.com/cyhhao/agentank-simulator**

示例：

```bash
# 在 agentank-simulator 目录
node bin/simulate-local.mjs \
  --bot-a ../agentank-framework/dist/bt-tank-boost.js \
  --bot-b ../agentank-framework/dist/bt-tank-cloak.js \
  --skill-a boost --skill-b cloak \
  --random-map --seed 42 --max-frames 300
```

## 架构概览

```text
【构建期 build-new.js】
  core-utils / bt-* / blackboard / nodes-* / tree-factory / entry
  + skill-params.js
  + skills/{skill}.js          ← 只拼 1 个技能
        ↓
  dist/bt-tank-{skill}.js  →  publish-new.py（默认 Terser 压缩）

【运行期 entry.js 每帧】
  refreshBlackboard → buildProfile → buildBehaviorTree? → root.tick(bb)
  → BT_DEBUG / PF 日志
```

### 根优先级（`ROOT_PRIORITY`）

数值越小越先 tick。骨架只保留通用大层，细策略自己往插槽里加：

```text
root Selector
├── cc-check              # 冰冻跳过
├── hard-survival         # 硬生存（+ 技能逃生插槽）
├── commit.early[]        # 技能 early 承诺插槽
├── soft-survival         # 软生存
├── commit.late[]         # 技能 late 承诺插槽
├── skill-attack          # 技能攻击插槽
├── attack / bomb-attack  # 通用攻击 / 放弹
├── skill-objective       # 技能目标插槽
├── objective             # 常规目标（含 pre/mid/post）
└── movement              # 移动 + 技能移动插槽
```

新增根层节点：在 [`tree-factory.js`](tree-factory.js) 常量表加一行，再挂入 `entries`。

### 技能插槽接口

每个 [`skills/{skill}.js`](skills/) 必须导出：

| 工厂 | 挂点 |
|------|------|
| `skillSurvivalNodes()` | 硬生存内嵌 |
| `skillCommitNodes()` | 根层 early / late |
| `skillAttackNodes(enemySkill)` | 根层技能攻击 |
| `skillObjectiveNodes(profile, enemySkill)` | `.layer` 根层；`.pre/mid/post` 嵌进 objective |
| `skillMovementNodes(profile)` | 嵌进 movement |

另需：`SKILL_NAME` / `MY_MATCHUP_OVERRIDES` / `SKILL_TUNING`。

[`skill-params.js`](skill-params.js) **不是节点**，而是对局参数字典：`DEFAULT_SKILL_PARAMS`（共用基线）+ 当前技能的 `MY_MATCHUP_OVERRIDES[敌技能]`，由 `getSkillMatchupParams(enemySkill)` 合并后给技能节点读距离/开关等，避免 Guard 里散落 magic number。

### 承诺机制

多帧锁定统一走 [`bt-commitment.js`](bt-commitment.js)：`bbCommit` 登记纯数据 → 根层 `CommitmentNode` 兑现。支持 `yieldWhen`（让位 + 步进禁区）与 `overrideSoftSurvival`（压过软生存）。

### 日志（务必保留格式）

| 格式 | 用途 |
|------|------|
| `f{frame} {path}:{action}` | BT_DEBUG 路径反查 |
| `PF f{frame} skill\|playstyle\|...` | 画像切换时间线（`match_analyzer` 解析） |

`bbSpeak` 带每局预算（约 32 次），关键动作可标 `important`。

## 目录结构

```text
agentank-framework/
├── bt-core.js / bt-commitment.js   # 行为树原语 + 承诺
├── blackboard.js / entry.js        # 黑板 + onIdle
├── tree-factory.js                 # ROOT_PRIORITY + 插槽组装
├── nodes-*.js                      # 各大层工厂（策略 TODO）
├── skills/*.js                     # 8 技能空插槽
├── core-utils.js / state-store.js  # 最小工具
├── movement-engine.js              # 移动执行（怎么走：安全步/BFS）
├── nodes-movement-v2.js            # 移动决策（何时走：追星/巡逻树）
├── build-new.js                    # 拼接构建
├── terser-config.js                # 发布压缩配置
├── publish-new.py                  # 发布（默认 minify）
├── match_runner.py                 # 线上批量对战
├── match_analyzer.py               # 对局采集入库（通用）
├── bt_profile_expectations.json    # Gap 规则（骨架为空）
├── bt_profile_expectations.example.json  # Gap 规则填写示例
├── analysis_db.py / report_server.py
├── analysis/static/                # 仪表盘（不含 data.db）
└── tank_profiles.py                # 档案（密钥走 .env）
```

## 如何填策略

1. 在 `nodes-survival.js` / `nodes-attack.js` 等用 `Sequence(Guard..., Action...)` 填叶子。
2. 在对应 `skills/{skill}.js` 填技能专属节点，挂到插槽。
3. 移动相关：决策写在 `nodes-movement-v2.js`（何时追星/巡逻），真正选步/go 放进 `movement-engine.js`（`moveToward` / `isSafeStep`），Action 里调用引擎，不要在 engine 里再挂行为树。
4. 需要多帧锁定时用 `bbCommit` + `CommitmentNode`，不要手搓 memory 旗标。
5. `node build-new.js --skill xxx` → 本地模拟器回归 → `publish-new.py` 发布。
6. 气泡台词与print注意平台审核；避免敏感内容。

朴素起步建议（自行实现）：

- 子弹威胁判断放在追星和开火之前；
- 同线无遮挡时先转向再开炮；
- 没射线也没星星时做安全巡逻，不要原地发呆。

## 开发迭代思路

1. **先定根优先级** — 生存 > 承诺 > 攻击 > 目标 > 移动；用常量表而不是注释半级编号。
2. **技能插槽化** — 底座固定，差异进 `skills/*`，构建期只拼一个技能做死代码消除。
3. **承诺收敛多帧行为** — 刺草/冲刺/补刀等统一兑现位，避免各写一套 TTL。
4. **日志契约** — `f…` / `PF…` 稳定后，分析器才能还原「这一帧为什么这么做」。
5. **本地模拟回归** — 同种子对比新旧；小样本噪声大，结论至少几十局量级。
6. **压缩过体积门** — 平台约 200KiB；改 `terser-config.js` 的 `INTERNAL_PROPS` 前先审计无字符串键访问。
7. **发布 → 对战 → 分析闭环** — `publish-new` → `match_runner` → `match_analyzer` → `report_server`。

## 发布 / 对战 / 分析

```bash
# 发布（默认 Terser 压缩；--no-minify 关闭）
python publish-new.py --only demo-teleport --notes "骨架试发"

# 批量排名对战（消耗平台次数；请人工确认后再跑）
python match_runner.py --only demo-teleport -n 10 --no-analyze

# 采集最近对局入库 + 仪表盘
python match_analyzer.py --limit 20
python report_server.py --db analysis/data.db
# 浏览器打开提示的本地地址
```

密钥：`.env` 中的 `AGENTANK_KEY_*`，见 [`.env.example`](.env.example)。

## 代码压缩

- 配置单一事实来源：[`terser-config.js`](terser-config.js)
- `publish-new.py` 默认调用 Terser API（`top_retain: onIdle`，`ecma: 5`，属性混淆白名单）
- 上限约 **200KiB**（UTF-8）；`--dry-run` 可看压缩后体积而不上传
- `--no-minify` 发布未压缩源码（调试用，易超限）

新增可混淆内部字段：优先 `_` 前缀，或审计后加入 `INTERNAL_PROPS`（禁止 `obj['field']` 动态访问）。

## License

MIT — 见 [LICENSE](LICENSE)。
