// ============================================================
// blackboard.js — 黑板骨架：感知上下文 + 动作包装 + speak 预算
//
// 框架版只保留字段与刷新主路径；惰性传感器 / 复杂战术缓存留给你填。
// BT_DEBUG 默认 true，便于回放对照路径日志。
// ============================================================

var BT_DEBUG = true;
var _BLACKBOARD = null;

/**
 * 获取或初始化黑板。帧数倒退视为新对局。
 */
function getBlackboard(game) {
  var frame = (game && game.frames) || 0;
  if (!_BLACKBOARD || frame < (_BLACKBOARD.lastFrame || 0) - 2) {
    _BLACKBOARD = {
      me: null,
      enemy: null,
      game: null,
      myPos: null,
      myDir: null,
      enemyTank: null,
      enemyPos: null,
      enemyBullets: [],
      bombs: [],
      frame: 0,
      star: null,
      gunIsReady: false,
      teleportIsReady: false,
      bombIsReady: false,
      mySkillType: (typeof BUILD_SKILL !== 'undefined' ? BUILD_SKILL : 'teleport'),
      skillIsReady: false,
      distToEnemy: 999,
      distToStar: 999,
      framesLeft: 128,
      myStars: 0,
      enmStars: 0,
      isLosing: false,
      isWinning: false,
      isTied: true,
      _cache: {},
      memory: null,
      profile: null,
      tree: null,
      profileFrame: -999,
      _treeSig: null,
      _trace: [],
      _lastAction: null,
      lastFrame: 0,
      _lastPfLine: null,
      shotDir: null
    };
  }
  return _BLACKBOARD;
}

/**
 * 每帧刷新黑板。节点 tick 前必须调用。
 */
function refreshBlackboard(bb, me, enemy, game) {
  bb.me = me;
  bb.enemy = enemy;
  bb.game = game;
  bb.frame = (game && game.frames) || 0;
  bb.lastFrame = bb.frame;
  bb.myPos = me.tank.position;
  bb.myDir = me.tank.direction;
  bb.enemyTank = (enemy && enemy.tank) ? enemy.tank : null;
  bb.enemyPos = bb.enemyTank ? bb.enemyTank.position : null;
  bb.enemyBullets = collectEnemyBullets(enemy);
  bb.star = game.star;
  bb.bombs = (game && game.bombs) || [];

  bb.gunIsReady = gunReady(me);
  bb.teleportIsReady = teleportReady(me);
  bb.bombIsReady = bombReady(me);
  bb.mySkillType = getMySkillType(me);
  bb.skillIsReady = skillReady(me);
  bb.shotDir = bb.enemyPos ? clearShotDirection(bb.myPos, bb.enemyPos, game) : null;
  bb.distToEnemy = bb.enemyPos ? manhattan(bb.myPos, bb.enemyPos) : 999;
  bb.distToStar = bb.star ? manhattan(bb.myPos, bb.star) : 999;
  bb.framesLeft = MAX_GAME_FRAMES - bb.frame;
  bb.myStars = (me && me.stars) || 0;
  bb.enmStars = (enemy && enemy.stars) || 0;
  bb.isLosing = bb.myStars < bb.enmStars;
  bb.isWinning = bb.myStars > bb.enmStars;
  bb.isTied = bb.myStars === bb.enmStars;

  bb._cache = {};
  bb._trace = [];
  bb._lastAction = null;

  bb.memory = getMatchState(game);
  // TODO: 在此挂载敌轨迹 / 草丛热力 / 幽灵弹等跨帧感知
  trackEnemy(bb.memory, bb.enemyTank, bb.myPos, game);
}

function bbFire(bb) {
  if (bb.me && typeof bb.me.fire === 'function') bb.me.fire();
}

function bbTurnToward(bb, dir) {
  turnToward(bb.me, dir, bb.game);
}

function bbThrowBomb(bb) {
  if (bb.me && typeof bb.me.throwBomb === 'function') bb.me.throwBomb();
  // TODO: 登记 myBombs + bbCommit('bomb-escape', ...) 做放弹撤离
}

var SPEAK_BUDGET_MAX = 30;
var SPEAK_RESERVE_THRESHOLD = 15;

/**
 * 统一气泡播报（带预算：每局约 32 次、每条 40 字、每帧 1 次）。
 * @param {boolean} [important] 预算紧张时仍放行
 */
function bbSpeak(bb, msg, important) {
  if (!(bb.me && typeof bb.me.speak === 'function')) return;
  var m = bb.memory;
  if (!m) { bb.me.speak(msg); return; }
  var used = m._speakUsed || 0;
  if (used >= SPEAK_BUDGET_MAX) return;
  if (used >= SPEAK_RESERVE_THRESHOLD && !important) return;
  if (m._speakFrame === bb.frame) return;
  m._speakFrame = bb.frame;
  m._speakUsed = used + 1;
  msg = String(msg);
  if (msg.length > 40) msg = msg.slice(0, 40);
  bb.me.speak(msg);
}

function bbUseSkill(bb, skillName, arg1, arg2) {
  if (!bb.me || !bb.me[skillName]) return;
  if (arg1 !== undefined && arg2 !== undefined) bb.me[skillName](arg1, arg2);
  else bb.me[skillName]();
}

function bbDirectGo(bb, target) {
  if (!target) return;
  if (bbMoveVetoedAt(target)) return;
  var dir = directionBetween(bb.myPos, target);
  if (dir === bb.myDir) bb.me.go();
  else if (dir) bbTurnToward(bb, dir);
}
