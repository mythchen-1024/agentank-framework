// ============================================================
// movement-engine.js — 移动「执行」骨架（无行为树节点）
//
// 和 nodes-movement-v2.js 的分工：
//   nodes-movement-v2 = 决策层：哪条 Sequence 拿到控制权、目标是星/草/巡逻点
//   本文件           = 执行层：目标格 → 安全邻步 → me.go() / turn
//
// 谁调用谁：
//   只有 Action 叶子（或 bbDirectGo 等包装）应调用本文件的 API；
//   不要在 engine 里再挂 Selector/Guard——那是 nodes-* 的事。
//
// 建议实现顺序：
//   1. isSafeStep：可走 + 步进否决 + 弹/雷威胁
//   2. moveToward：朝目标选一步（BFS / 评分），再 go 或 turn
// ============================================================

/**
 * 判断一步是否安全可走。
 * 使用位置：moveToward 选邻格、巡逻/追星 Action 预检。
 */
function isSafeStep(pos, game, bullets, bombs, memory, frame) {
  if (!isPassable(pos, game)) return false;
  if (bbMoveVetoedAt(pos)) return false;
  // TODO: 叠加 fireThreatAt / bombThreatAt（见 threat-eval.js）
  return true;
}

/**
 * 朝 target 走一步（或先转向）。
 * 使用位置：nodes-movement-v2 / skills 移动插槽的 Action 内。
 * @param {Object} opts 可选：避敌、星拉力、禁止纯转向等（自行约定）
 */
function moveToward(me, target, game, opts) {
  // TODO: BFS / 评分选邻格 → 同向则 me.go()，否则 turnToward
}
