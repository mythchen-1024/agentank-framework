// ============================================================
// nodes-survival.js — 硬/软生存节点骨架
//
// 参考：真威胁逃生应优先于攻击与抢星。
//
// 插槽怎么接（看 tree-factory.js）：
//   var s = skillSurvivalNodes();  // 来自当前构建的 skills/{skill}.js
//   createHardSurvivalTree(s.shieldBlock, s.deferredEscape, s.shieldHold);
// 三个字段都是「可选 BT 节点」：技能无关则返回 null，Selector 会自动跳过。
// 示例写法见 skills/shield.js 里的注释。
// ============================================================

/**
 * 硬生存子树。
 * @param {Object|null} shieldBlock    技能挡弹/开盾节点（如 shield）
 * @param {Object|null} deferredEscape 物理躲避失败后的技能逃生（如 boost/tp）
 * @param {Object|null} shieldHold     持盾窗口内的行为（可选）
 */
function createHardSurvivalTree(shieldBlock, deferredEscape, shieldHold) {
  return Selector('hard-survival', [
    // TODO: 底座通用躲弹/躲雷（与技能无关）
    // Sequence('bullet-dodge', [
    //   Guard('has-threat', function (bb) { return /* 有来弹 */ false; }),
    //   Action('do-bullet-dodge', function (bb) { /* bb.me.go / turn */ })
    // ]),
    shieldHold || null,
    shieldBlock || null,
    deferredEscape || null
  ]);
}

/**
 * 软生存：预防性规避。
 * override 承诺激活时应整棵让位（见 bbAnyOverridingCommit）。
 */
function createSoftSurvivalTree(profile) {
  return Selector('soft-survival', [
    Sequence('soft-override-yield', [
      Guard('has-overriding-commit', function (bb) {
        return bbAnyOverridingCommit(bb);
      }),
      // 有压制承诺时主动 FAILURE，把帧让给 commit.late
      Guard('always-fail', function () { return false; })
    ])
    // TODO: aim-dodge 等预防性节点
  ]);
}
