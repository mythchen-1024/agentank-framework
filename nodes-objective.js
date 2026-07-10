// ============================================================
// nodes-objective.js — 目标层骨架（守星 / 拦截 / 刺杀）
//
// slots 来自 skillObjectiveNodes：{ layer, pre, mid, post }
// ============================================================

function createObjectiveTree(profile, slots) {
  slots = slots || { layer: null, pre: [], mid: [], post: [] };
  var children = [];

  // 技能目标层也可挂在根层 SKILL_OBJECTIVE；此处挂 pre/mid/post 插入点
  var i;
  if (slots.pre) {
    for (i = 0; i < slots.pre.length; i++) children.push(slots.pre[i]);
  }

  // TODO: Sequence('star-guard', [...])
  // TODO: Sequence('intercept', [...])
  // TODO: Sequence('assassination', [...])

  if (slots.mid) {
    for (i = 0; i < slots.mid.length; i++) children.push(slots.mid[i]);
  }
  if (slots.post) {
    for (i = 0; i < slots.post.length; i++) children.push(slots.post[i]);
  }

  return Selector('objective', children);
}
