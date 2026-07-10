// ============================================================
// entry.js — onIdle 入口（框架骨架）
//
// 每帧：刷新黑板 → 观察 → Profile/树重建 → tick → 调试日志
// 日志契约（match_analyzer 依赖，勿随意改格式）：
//   f{frame} {path}:{action}
//   PF f{frame} {skill}|{playstyle}|{attack}|{star}|{standoff}
// ============================================================

var PROFILE_REBUILD_INTERVAL = 16;

function treeSignature(profile, mySkillType) {
  if (!profile) return '';
  return mySkillType + '|' +
    profile.skillType + '|' +
    profile.attackAggression + '|' +
    profile.starAggression + '|' +
    profile.standoffDistance + '|' +
    (profile.enableAssassination ? 1 : 0) + '|' +
    (profile.bushCamp ? 1 : 0) + '|' +
    (profile.bushCamperDefense ? 1 : 0) + '|' +
    (profile.dodgeBand ? 1 : 0) + '|' +
    (profile.freezeZoneAvoid ? 1 : 0) + '|' +
    (profile.prefireOnDisappear ? 1 : 0) + '|' +
    (profile.shieldBait ? 1 : 0);
}

function onIdle(me, enemy, game) {
  // 最外层兜底：未捕获异常会导致平台 battle function failed
  try {
    _onIdleBody(me, enemy, game);
  } catch (e) {
    try {
      if (typeof print === 'function') {
        var _em = e && e.message ? e.message : String(e);
        var _en = e && e.name ? e.name : 'Error';
        print('onIdle-catch ' + _en + ': ' + _em);
      }
    } catch (_ignore) { /* 兜底也失败则本帧空过 */ }
  }
}

function _onIdleBody(me, enemy, game) {
  var bb = getBlackboard(game);
  refreshBlackboard(bb, me, enemy, game);

  updatePlaystyleObservation(bb);

  var needProfile = !bb.profile ||
    bb.frame - bb.profileFrame >= PROFILE_REBUILD_INTERVAL;

  if (needProfile) {
    bb.profile = buildProfile(bb);
    bb.profileFrame = bb.frame;
    var sig = treeSignature(bb.profile, bb.mySkillType);
    if (!bb.tree || sig !== bb._treeSig) {
      bb.tree = buildBehaviorTree(bb.profile);
      bb._treeSig = sig;
    }
    // PF 遥测：画像变化时输出，供分析器还原时间线
    var pfLine = bb.profile.skillType + '|' + bb.profile.playstyle + '|' +
      bb.profile.attackAggression + '|' + bb.profile.starAggression + '|' +
      bb.profile.standoffDistance;
    if (pfLine !== bb._lastPfLine) {
      bb._lastPfLine = pfLine;
      if (typeof print === 'function') print('PF f' + bb.frame + ' ' + pfLine);
    }
  }

  bb.tree.tick(bb);

  // BT_DEBUG：路径 + 动作名，便于从 replay logs 反查哪条分支拿到控制权
  if (BT_DEBUG && bb._lastAction) {
    var traceMsg = bb._trace.join('>') + ':' + bb._lastAction;
    if (bb.frame === 5 && bb.profile) {
      traceMsg = '我:' + bb.mySkillType + ' vs 敌:' + bb.profile.name + ' | ' + traceMsg;
    }
    if (isKeyActionForSpeak(bb._lastAction)) {
      bbSpeak(bb, traceMsg.length > 30 ? bb._lastAction : traceMsg, true);
    }
    if (typeof print === 'function') {
      print('f' + bb.frame + ' ' + traceMsg);
    }
  }
}

function isKeyActionForSpeak(actionName) {
  // 骨架：按需扩展你关心的 Action 名
  var keyActions = {
    'frozen-wait': true,
    'do-patrol': true
  };
  return !!keyActions[actionName];
}
