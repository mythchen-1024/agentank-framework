// terser-config.js — 发布压缩配置的单一事实来源。
//
// 消费方：publish-new.py 的 _minify_js_terser_api（发布默认 minify）。
//
// 约束：top_retain + reserved 保留平台入口 onIdle；其余顶层全部混淆。
// 平台代码上限约 200KiB（UTF-8 字节）；ecma:5 避免压缩注入 ?? / 可选链导致沙箱异常。

// ── 属性名混淆白名单 ──
// 挑选标准（缺一不可）：
//   1. 与引擎 API 属性名零重叠；
//   2. 无 obj['field'] 字符串键访问；
//   3. 无 JSON 序列化/跨局持久化依赖。
// 新增内部字段：优先用 _ 前缀；或审计后加进此列表。
var INTERNAL_PROPS = [
  // bb 每帧感知字段（blackboard.js）
  'enemyPos', 'enemyTank', 'enemyBullets', 'myPos', 'myDir', 'frame',
  'enemy', 'game', 'memory', 'profile', 'tree', 'profileFrame', 'lastFrame',
  'distToEnemy', 'distToStar', 'framesLeft', 'myStars', 'enmStars',
  'isLosing', 'isWinning', 'isTied', 'gunIsReady', 'teleportIsReady',
  'bombIsReady', 'skillIsReady', 'mySkillType', 'shotDir',
  // state-store 跨帧记忆字段
  'lastEnemyPos', 'stuckFrames', 'spinFrames', 'commitments',
  // profile 内部键
  'attackAggression', 'starAggression', 'standoffDistance',
  // 行为树节点内部结构
  'tick', 'children',
];

module.exports = {
  compress: {
    toplevel: true,
    top_retain: ['onIdle'],
    passes: 5,
    // ecma 5：禁止压缩阶段注入 ?? / 可选链（平台沙箱偶发 HTTP 400）
    ecma: 5,
  },
  mangle: {
    toplevel: true,
    reserved: ['onIdle'],
    properties: {
      regex: new RegExp('^_|^(?:' + INTERNAL_PROPS.join('|') + ')$'),
      builtins: true,
      reserved: [
        'onIdle', 'tank', 'position', 'direction', 'status', 'skill', 'type',
        'bullet', 'bombs', 'frames', 'map', 'star', 'speak', 'fire', 'go', 'turn',
        'teleport', 'remainingCooldownFrames', 'remainingFrames', 'fireLocked',
        'shielded', 'boosted', 'frozen', 'stunned', 'overloaded', 'cloaked',
        'detonateFrame', 'dx', 'dy', 'up', 'down', 'left', 'right', 'name',
        'length', 'push', 'slice', 'concat', 'indexOf', 'hasOwnProperty',
        'call', 'apply', 'bind', 'then', 'keys', 'values', 'entries',
        'toString', 'valueOf', 'constructor', 'prototype', 'message', 'stack', 'code',
        'floor', 'ceil', 'abs', 'min', 'max', 'round', 'random', 'sqrt', 'pow',
        'log', 'sin', 'cos',
      ],
    },
  },
  format: {
    comments: false,
    ecma: 5,
  },
};
