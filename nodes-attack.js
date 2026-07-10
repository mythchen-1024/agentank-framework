// ============================================================
// nodes-attack.js — 攻击 / 炸弹节点骨架
// ============================================================

/**
 * 通用炮弹攻击树。
 * profile.attackAggression === 'none' 时可返回 null。
 */
function createAttackTree(profile) {
  if (profile && profile.attackAggression === 'none') return null;
  return Selector('attack', [
    // TODO: Sequence('open-shot', [Guard(...), Action('do-fire-direct', ...)])
    // TODO: Sequence('guard-line', [...])
  ]);
}

/** 主动放弹。 */
function createBombNodes(profile) {
  return Selector('bomb-attack', [
    // TODO: Sequence('place-bomb', [Guard(...), Action('do-throw-bomb', ...)])
  ]);
}
