// skills/shield.js — 技能插槽骨架（构建期只拼本文件）
// 接口：SKILL_NAME / MY_MATCHUP_OVERRIDES / SKILL_TUNING
//       skillSurvivalNodes / skillCommitNodes / skillAttackNodes
//       skillObjectiveNodes / skillMovementNodes
//
// ── skillSurvivalNodes 示例（勿直接照抄上线，仅说明形状）──
// function skillSurvivalNodes() {
//   var shieldBlock = Sequence('shield-block', [
//     Guard('bullet-incoming', function (bb) {
//       return /* 有来弹威胁 */ false;
//     }),
//     Guard('shield-ready', function (bb) { return bb.skillIsReady; }),
//     Action('do-shield-block', function (bb) {
//       bbSpeak(bb, '盾!', true);
//       bbUseSkill(bb, 'shield');
//     })
//   ]);
//   // 无持盾/延迟逃生时保持 null
//   return { shieldHold: null, shieldBlock: shieldBlock, deferredEscape: null };
// }
// 其它技能同理：boost 可填 deferredEscape；teleport 可填逃生 TP；用不到的槽位留 null。

var SKILL_NAME = 'shield';
var MY_MATCHUP_OVERRIDES = {};
var SKILL_TUNING = {};

function skillSurvivalNodes() {
  return { shieldHold: null, shieldBlock: null, deferredEscape: null };
}

function skillCommitNodes() {
  return { early: [], late: [] };
}

function skillAttackNodes(enemySkillType) {
  return Selector('skill-attack', []);
}

function skillObjectiveNodes(profile, enemySkillType) {
  return { layer: Selector('skill-objective', []), pre: [], mid: [], post: [] };
}

function skillMovementNodes(profile) {
  return [];
}
