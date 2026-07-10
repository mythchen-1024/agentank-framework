// ============================================================
// bt-commitment.js — 通用承诺（Commitment）机制（框架骨架）
//
// 多帧锁定行为统一为：动作里 bbCommit 登记纯数据 → 根层 CommitmentNode 兑现。
//
// 语义：
//   无承诺 / 过期 / abortIf → FAILURE（并清承诺）
//   yieldWhen → 注册 constraint 步进否决 + FAILURE（让位下层，保留禁区）
//   when 为假 → FAILURE（本帧不可执行，保留承诺）
//   否则 act → SUCCESS
// ============================================================

function bbCommit(bb, name, data, ttl) {
  if (!bb.memory.commitments) bb.memory.commitments = {};
  bb.memory.commitments[name] = { name: name, frame: bb.frame, ttl: ttl, data: data || {} };
}

function bbGetCommitment(bb, name) {
  return (bb.memory.commitments && bb.memory.commitments[name]) || null;
}

function bbClearCommitment(bb, name) {
  if (bb.memory.commitments) bb.memory.commitments[name] = null;
}

/** 注册本帧步进否决：fn(pos) 为 true 表示禁止走入该格。 */
function bbRegisterMoveVeto(bb, fn) {
  if (!fn) return;
  (bb._cache._moveVetoes || (bb._cache._moveVetoes = [])).push(fn);
}

/** 查询某格是否被本帧步进否决。 */
function bbMoveVetoedAt(pos) {
  var bb = (typeof _BLACKBOARD !== 'undefined' && _BLACKBOARD) ? _BLACKBOARD : null;
  var vetoes = bb && bb._cache && bb._cache._moveVetoes;
  if (!vetoes || !vetoes.length) return false;
  for (var i = 0; i < vetoes.length; i++) {
    if (vetoes[i](pos)) return true;
  }
  return false;
}

// 声明 overrideSoftSurvival 的承诺登记于此，供软生存入口统一让位
var COMMIT_OVERRIDE_CHECKS = {};

function bbAnyOverridingCommit(bb) {
  var all = bb.memory && bb.memory.commitments;
  if (!all) return false;
  for (var name in all) {
    if (!all.hasOwnProperty(name)) continue;
    var c = all[name];
    if (!c) continue;
    if (c.ttl != null && bb.frame - c.frame > c.ttl) continue;
    var check = COMMIT_OVERRIDE_CHECKS[name];
    if (check && check(bb, c)) return true;
  }
  return false;
}

/**
 * 承诺兑现节点。挂在根层 commit.early / commit.late 插槽。
 * @param {Object} spec { when?, abortIf?, yieldWhen?, constraint?, overrideSoftSurvival?, act, actionName? }
 */
function CommitmentNode(label, name, spec) {
  if (spec.overrideSoftSurvival) {
    COMMIT_OVERRIDE_CHECKS[name] = spec.overrideSoftSurvival;
  }
  return {
    type: 'commitment', name: label,
    tick: function (bb) {
      var c = bbGetCommitment(bb, name);
      if (!c) return BT_FAILURE;
      if (c.ttl != null && bb.frame - c.frame > c.ttl) {
        bbClearCommitment(bb, name);
        return BT_FAILURE;
      }
      if (spec.abortIf && spec.abortIf(bb, c)) {
        bbClearCommitment(bb, name);
        return BT_FAILURE;
      }
      if (spec.yieldWhen && spec.yieldWhen(bb, c)) {
        if (spec.constraint) bbRegisterMoveVeto(bb, spec.constraint(bb, c));
        return BT_FAILURE;
      }
      if (spec.when && !spec.when(bb, c)) return BT_FAILURE;
      // 骨架：用户在 skills/* 里实现 act；此处若未提供则空过
      if (typeof spec.act === 'function') spec.act(bb, c);
      bb._lastAction = spec.actionName || label;
      return BT_SUCCESS;
    }
  };
}
