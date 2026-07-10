// ============================================================
// state-store.js — 跨帧记忆骨架
// ============================================================

var MATCH_STATE = null;

function getMatchState(game) {
  var frame = (game && game.frames) || 0;
  if (!MATCH_STATE || frame < (MATCH_STATE._lastFrame || 0) - 2) {
    MATCH_STATE = {
      _lastFrame: frame,
      commitments: {},
      _speakUsed: 0,
      _speakFrame: -1,
      lastEnemyPos: null,
      stuckFrames: 0,
      spinFrames: 0
    };
  }
  MATCH_STATE._lastFrame = frame;
  return MATCH_STATE;
}

/** 记录敌方位置等基础轨迹。TODO: 扩展草丛热力 / 开火车道。 */
function trackEnemy(memory, enemyTank, myPos, game) {
  if (!memory) return;
  if (enemyTank && enemyTank.position) {
    memory.lastEnemyPos = enemyTank.position.slice();
  }
}
