// ============================================================
// enemy-profiler.js — 敌方 Profile 骨架
//
// 实战中会识别敌技能 + 打法 trait，驱动树结构签名。
// 框架版返回固定默认画像，保证 treeSignature / 插槽可跑通。
// ============================================================

function updatePlaystyleObservation(bb) {
  // TODO: 统计敌移动/开火/抢星/蹲草特征
}

function buildProfile(bb) {
  var skillType = 'unknown';
  if (bb.enemy && bb.enemy.skill && bb.enemy.skill.type) {
    skillType = bb.enemy.skill.type;
  }
  return {
    name: skillType,
    skillType: skillType,
    playstyle: 'default',
    attackAggression: 'normal',
    starAggression: 'normal',
    standoffDistance: 4,
    enableAssassination: false,
    bushCamp: false,
    bushCamperDefense: false,
    dodgeBand: true,
    freezeZoneAvoid: false,
    prefireOnDisappear: false,
    shieldBait: false
  };
}
