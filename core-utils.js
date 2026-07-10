// ============================================================
// core-utils.js — 最小几何 / 地图 / 技能就绪工具（框架骨架）
//
// 只保留拼装与黑板刷新所需的基础函数；战术搜索请自行扩展。
// ============================================================

var MAX_GAME_FRAMES = 128;
var BOMB_FUSE_FRAMES = 5;

var DIRS = ['up', 'down', 'left', 'right'];
var DIR_DELTA = {
  up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0]
};

function manhattan(a, b) {
  if (!a || !b) return 999;
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
}

function samePos(a, b) {
  return !!(a && b && a[0] === b[0] && a[1] === b[1]);
}

function directionBetween(from, to) {
  if (!from || !to) return null;
  var dx = to[0] - from[0];
  var dy = to[1] - from[1];
  if (dx === 0 && dy === 0) return null;
  if (Math.abs(dx) >= Math.abs(dy)) return dx > 0 ? 'right' : 'left';
  return dy > 0 ? 'down' : 'up';
}

function neighborPos(pos, dir) {
  var d = DIR_DELTA[dir];
  if (!d || !pos) return null;
  return [pos[0] + d[0], pos[1] + d[1]];
}

function inBounds(pos, game) {
  if (!pos || !game || !game.map) return false;
  var row = game.map[pos[1]];
  return pos[1] >= 0 && pos[1] < game.map.length &&
    pos[0] >= 0 && row && pos[0] < row.length;
}

function isPassable(pos, game) {
  if (!inBounds(pos, game)) return false;
  var cell = game.map[pos[1]][pos[0]];
  // 约定：0 空地可走；具体地图编码以平台文档为准
  return cell === 0 || cell === '0' || cell === '.';
}

function gunReady(me) {
  return !!(me && me.tank && !me.tank.fireLocked &&
    !(me.bullet && me.bullet.position));
}

function bombReady(me) {
  return !!(me && typeof me.throwBomb === 'function' &&
    (!me.bombCooldown || me.bombCooldown <= 0));
}

function teleportReady(me) {
  return skillOfTypeReady(me, 'teleport');
}

function skillReady(me) {
  if (!me || !me.skill) return false;
  var cd = me.skill.remainingCooldownFrames;
  return cd == null || cd <= 0;
}

function skillOfTypeReady(me, type) {
  if (!me || !me.skill || me.skill.type !== type) return false;
  return skillReady(me);
}

function getMySkillType(me) {
  if (typeof BUILD_SKILL !== 'undefined' && BUILD_SKILL) return BUILD_SKILL;
  if (me && me.skill && me.skill.type) return me.skill.type;
  return 'teleport';
}

function collectEnemyBullets(enemy) {
  if (!enemy) return [];
  var out = [];
  if (enemy.bullet && enemy.bullet.position) out.push(enemy.bullet);
  if (enemy.bullets && enemy.bullets.length) {
    for (var i = 0; i < enemy.bullets.length; i++) {
      if (enemy.bullets[i] && enemy.bullets[i].position) out.push(enemy.bullets[i]);
    }
  }
  return out;
}

/**
 * 两点间是否有直线空射（无墙遮挡）。骨架：仅同行/同列且中间格可走。
 * TODO: 补完整弹道/草丛遮挡规则。
 */
function clearShotDirection(from, to, game) {
  if (!from || !to) return null;
  if (from[0] !== to[0] && from[1] !== to[1]) return null;
  var dir = directionBetween(from, to);
  if (!dir) return null;
  var cur = neighborPos(from, dir);
  while (cur && !samePos(cur, to)) {
    if (!isPassable(cur, game)) return null;
    cur = neighborPos(cur, dir);
  }
  return samePos(cur, to) ? dir : null;
}

function canShoot(me, enemy) {
  if (!me || !enemy || !enemy.tank) return false;
  return !!clearShotDirection(me.tank.position, enemy.tank.position, null);
}

function turnToward(me, dir, game) {
  if (!me || !me.tank || !dir) return;
  var cur = me.tank.direction;
  if (cur === dir) return;
  // 平台 turn 只认 left/right 相对转向
  var order = ['up', 'right', 'down', 'left'];
  var ci = order.indexOf(cur);
  var ti = order.indexOf(dir);
  if (ci < 0 || ti < 0) return;
  var diff = (ti - ci + 4) % 4;
  if (diff === 1) me.turn('right');
  else if (diff === 3) me.turn('left');
  else if (diff === 2) me.turn('right'); // 180°：先转一格，下帧再转
}

function iAmHidden(me, game) {
  return !!(me && me.status && me.status.cloaked);
}

/** 脱困占位：骨架不实现，entry catch 里可选调用。 */
function breakStuckStep(me, game, enemyPos, enemyTank, bullets, lastPos, enemy, memory) {
  // TODO: 选安全邻格 go / turn
}
