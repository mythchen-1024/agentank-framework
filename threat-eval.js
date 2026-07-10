// ============================================================
// threat-eval.js — 威胁评估层骨架
//
// 实战中 fireThreatAt / bombThreatAt 是躲弹与走位的统一口径。
// 框架版返回「无威胁」，避免 stub 节点误依赖。
// ============================================================

function fireThreatAt(pos, bullets, game, memory, enemyTank) {
  // TODO: 判断 pos 是否会被现有/预测弹命中
  return false;
}

function bombThreatAt(pos, bombs, game, frame) {
  // TODO: 判断 pos 是否在引信内爆区
  return false;
}

function anyBulletThreatens(bullets, pos, game) {
  return false;
}
