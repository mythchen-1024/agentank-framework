# 行为树快速入门（框架骨架）

完整工程说明见 [`README.md`](README.md)。本文只解决：打开代码后如何看懂「这一帧为什么会做这个动作」。

## 5 分钟先看懂

每一帧平台只调用一次 [`entry.js`](entry.js) 的 `onIdle(me, enemy, game)`：

```text
onIdle
├── refreshBlackboard(bb, me, enemy, game)
├── updatePlaystyleObservation(bb)
├── buildProfile(bb)                 # 每 16 帧节流
├── buildBehaviorTree(profile)?      # 仅 treeSignature 变化时重建
└── bb.tree.tick(bb)                 # Selector 从上到下，第一个成功即停
    └── BT_DEBUG → print('f{frame} path:action')
```

节点语义（[`bt-core.js`](bt-core.js)）：

| 节点 | 含义 |
|------|------|
| `Selector` | 同层互斥，越靠前优先级越高 |
| `Sequence` | Guard 全过才执行 Action |
| `Guard` | 纯条件 |
| `Action` | 发出 fire/go/turn/skill/speak |
| `When` | 条件为真才启用整棵子树 |
| `CommitmentNode` | 兑现跨帧承诺（见 `bt-commitment.js`） |

排查动作时问两句：

1. 更高优先级的兄弟为什么没成功？
2. 当前 Sequence 里每个 Guard 为什么通过或失败？

## 根树顺序

由 [`tree-factory.js`](tree-factory.js) 的 `ROOT_PRIORITY` 排序。骨架保留插槽与挂点；策略叶子为 TODO。

```text
cc-check → hard-survival → commit.early → soft-survival → commit.late
→ skill-attack → attack → bomb-attack
→ skill-objective → objective → movement
```

## 插槽怎么挂

```text
【构建期】只拼 skills/{当前技能}.js
【运行期】敌方技能进 profile.skillType，传给 skillAttack / skillObjective 做 matchup

根层插槽：commit.early / commit.late / skillAttack / skillObjective.layer
内嵌插槽：hard-survival ← skillSurvivalNodes
          objective ← pre/mid/post
          movement ← skillMovementNodes
```

## 日志反查

开启 `BT_DEBUG`（骨架默认 `true`）后，回放 raw logs 中：

```text
f42 hard-survival>bullet-dodge:do-bullet-dodge
PF f0 teleport|default|normal|normal|4
```

- `>` 连接的是 Selector 选中的子节点名路径
- `:` 后是最终 Action 名
- `PF` 行是画像切换，供 `match_analyzer` 解析

## 填叶子时的约定

- 真来弹 / 炸弹威胁放在追星和开火之前
- 同线无遮挡：先转向再开炮
- 无目标时做安全巡逻，避免空转
- 多帧锁定用 `bbCommit`，不要散落 memory 旗标
- 移动：`nodes-movement-v2` 管「何时/去哪」，`movement-engine` 管「怎么走」；Action 调 `moveToward`
