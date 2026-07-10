// skills/teleport.js — 技能插槽骨架（构建期只拼本文件）
// skillSurvivalNodes 填法示例见 skills/shield.js 顶部注释

var SKILL_NAME = 'teleport';
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
