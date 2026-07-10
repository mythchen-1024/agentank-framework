// ============================================================
// tree-factory.js — 固定骨架 + 技能插槽组装（框架骨架）
//
// 技能插槽（由 skills/{skill}.js 提供）：
//   skillSurvivalNodes()           → 硬生存内嵌
//   skillCommitNodes().early/late  → 根层承诺
//   skillAttackNodes(enemySkill)
//   skillObjectiveNodes(...).layer/pre/mid/post
//   skillMovementNodes(profile)    → 移动层内嵌
//
// ROOT_PRIORITY：数值越小越先 tick。新增根层节点 = 常量表加一行。
// ============================================================

var ROOT_PRIORITY = {
  CC_CHECK:         100,
  HARD_SURVIVAL:    200,
  COMMIT_EARLY:     250,
  SOFT_SURVIVAL:    400,
  COMMIT_LATE:      450,
  SKILL_ATTACK:     600,
  ATTACK:           700,
  BOMB_ATTACK:      800,
  SKILL_OBJECTIVE:  900,
  OBJECTIVE:       1000,
  MOVEMENT:        1100
};

/**
 * 根据 Profile 组装完整行为树。
 * 我方技能在构建期固定（skills/{skill}.js 已拼入）。
 */
function buildBehaviorTree(profile) {
  var enemySkillType = (profile && profile.skillType) || 'unknown';
  var survival = skillSurvivalNodes();
  var commit = skillCommitNodes();
  var hardSurvival = createHardSurvivalTree(
    survival.shieldBlock, survival.deferredEscape, survival.shieldHold
  );
  var softSurvival = createSoftSurvivalTree(profile);
  var skillAttack = skillAttackNodes(enemySkillType);
  var attack = createAttackTree(profile);
  var bombAttack = createBombNodes(profile);
  var skillObj = skillObjectiveNodes(profile, enemySkillType);
  var objective = createObjectiveTree(profile, skillObj);
  var movement = createMovementTree(profile);

  // 被控拦截：冰冻时本帧无法操作（平台机制，非策略）
  var ccCheck = Sequence('cc-check', [
    Guard('is-frozen', function (bb) {
      return !!(bb.me.status && bb.me.status.frozen);
    }),
    Action('frozen-wait', function (bb) {
      bbSpeak(bb, '冻');
    })
  ]);

  var entries = [
    { p: ROOT_PRIORITY.CC_CHECK, n: ccCheck },
    { p: ROOT_PRIORITY.HARD_SURVIVAL, n: hardSurvival },
    { p: ROOT_PRIORITY.SOFT_SURVIVAL, n: softSurvival },
    { p: ROOT_PRIORITY.SKILL_ATTACK, n: skillAttack },
    { p: ROOT_PRIORITY.ATTACK, n: attack },
    { p: ROOT_PRIORITY.BOMB_ATTACK, n: bombAttack },
    { p: ROOT_PRIORITY.SKILL_OBJECTIVE, n: skillObj.layer },
    { p: ROOT_PRIORITY.OBJECTIVE, n: objective },
    { p: ROOT_PRIORITY.MOVEMENT, n: movement }
  ];

  // 技能承诺插槽：early 在软生存前，late 在软生存后
  var ci;
  for (ci = 0; ci < commit.early.length; ci++) {
    entries.push({ p: ROOT_PRIORITY.COMMIT_EARLY, n: commit.early[ci], sub: ci });
  }
  for (ci = 0; ci < commit.late.length; ci++) {
    entries.push({ p: ROOT_PRIORITY.COMMIT_LATE, n: commit.late[ci], sub: ci });
  }

  entries.sort(function (a, b) {
    return a.p - b.p || (a.sub || 0) - (b.sub || 0);
  });

  var filtered = [];
  for (var i = 0; i < entries.length; i++) {
    if (entries[i].n) filtered.push(entries[i].n);
  }

  return Selector('root', filtered);
}
