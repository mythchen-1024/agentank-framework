// ============================================================
// skill-params.js — 「我对谁」的对局参数表（不是行为树节点）
//
// 解决什么问题：
//   同一套技能节点里，对 overload / shield / teleport 敌往往要用不同距离、
//   激进度等数字。若写死在 Guard 里会散落 magic number，改一处漏三处。
//   这里集中成「参数字典」，节点只读 getSkillMatchupParams(敌技能)。
//
// 两层合并（后者覆盖前者）：
//   1. DEFAULT_SKILL_PARAMS     — 所有我方技能共用的基线（本文件）
//   2. MY_MATCHUP_OVERRIDES[敌] — 当前构建技能的专属覆盖（skills/{skill}.js）
//
// 谁在什么时候读：
//   skills/* 的 skillAttackNodes / skillObjectiveNodes 等拿到 enemySkillType 后：
//     var mp = getSkillMatchupParams(enemySkillType);
//     if (bb.distToEnemy <= mp.chaseRange) { ... }
//
// 示例（注释，不生效）：
//   var DEFAULT_SKILL_PARAMS = { chaseRange: 5, rushRaceBypass: true };
//   // skills/boost.js:
//   // var MY_MATCHUP_OVERRIDES = {
//   //   overload: { chaseRange: 4, rushRaceBypass: false },
//   //   freeze:   { chaseRange: 7 }
//   // };
//   // → 对 overload 敌：chaseRange=4；对其它敌：chaseRange=5
// ============================================================

var DEFAULT_SKILL_PARAMS = {
  // 按需加键，例如：chaseRange: 5
};

/**
 * 取「当前我方技能 vs 该敌方技能」的合并参数。
 * @param {string} enemySkillType 敌方技能名（来自 profile.skillType）
 * @returns {Object} 浅拷贝后的参数表，可安全改本地副本
 */
function getSkillMatchupParams(enemySkillType) {
  var base = {};
  var k;
  for (k in DEFAULT_SKILL_PARAMS) {
    if (DEFAULT_SKILL_PARAMS.hasOwnProperty(k)) base[k] = DEFAULT_SKILL_PARAMS[k];
  }
  var overrides = (typeof MY_MATCHUP_OVERRIDES !== 'undefined' && MY_MATCHUP_OVERRIDES) || {};
  var specific = overrides[enemySkillType] || {};
  for (k in specific) {
    if (specific.hasOwnProperty(k)) base[k] = specific[k];
  }
  return base;
}
