// ============================================================
// nodes-movement-v2.js — 移动层「决策」骨架（行为树节点）
//
// 和 movement-engine.js 的分工：
//   本文件 = 何时 / 去哪（Selector 里挂 chase-star、巡逻等 Sequence）
//   movement-engine.js = 怎么走（安全邻格、BFS/评分、真正 go/turn）
//
// 典型调用链：
//   createMovementTree → Action 里调用 moveToward / isSafeStep
//   tree-factory 把本树挂在 ROOT_PRIORITY.MOVEMENT（整棵树最后兜底）
//
// 技能移动插槽：skillMovementNodes(profile) 返回的节点插在通用移动之前。
// 参考：没射线也没星星时不要原地发呆——至少实现巡逻。
// ============================================================

function createMovementTree(profile) {
  var skillMoves = [];
  if (typeof skillMovementNodes === 'function') {
    skillMoves = skillMovementNodes(profile) || [];
  }

  var children = [];
  var i;
  for (i = 0; i < skillMoves.length; i++) children.push(skillMoves[i]);

  // TODO: Sequence('chase-star', [
  //   Guard('has-star', function (bb) { return !!bb.star; }),
  //   Action('do-chase-star', function (bb) {
  //     moveToward(bb.me, bb.star, bb.game, { /* opts */ });
  //   })
  // ])
  // TODO: Sequence('bush-hold', [...]) / hold-lane / dig-wall

  // 骨架兜底：显式 FAILURE 占位，提醒实现者补巡逻
  children.push(Sequence('patrol-todo', [
    Guard('todo-patrol', function () {
      // TODO: 实现安全巡逻；当前故意 FAILURE
      return false;
    }),
    Action('do-patrol', function (bb) {
      bbSpeak(bb, '巡');
      // TODO: 选巡逻目标后 moveToward(bb.me, target, bb.game, ...)
    })
  ]));

  return Selector('movement', children);
}
