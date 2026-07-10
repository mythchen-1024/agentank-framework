/**
 * build-new.js — 将行为树各模块拼接为单一可提交 js。
 *
 * 用法：
 *   node build-new.js --all-skills
 *   node build-new.js --skill teleport
 *   node build-new.js --skill shield,boost
 *
 * 输出：dist/bt-tank-{skill}.js
 */

const fs = require('fs');
const path = require('path');
const dir = __dirname;

const SKILLS = ['teleport', 'shield', 'boost', 'overload', 'stun', 'freeze', 'cloak', 'poison'];
const OUT_DIR = path.join(dir, 'dist');
const LEGACY_OUT = path.join(dir, 'bt-tank-submit.js');

// 框架版 MODULE_ORDER：无 tactics-*；后加载可引用前文件符号
const MODULE_ORDER = [
  { file: 'core-utils.js', base: dir },
  { file: 'movement-engine.js', base: dir },
  { file: 'threat-eval.js', base: dir },
  { file: 'state-store.js', base: dir },
  { file: 'bt-core.js', base: dir },
  { file: 'bt-commitment.js', base: dir },
  { file: 'blackboard.js', base: dir },
  { file: 'enemy-profiler.js', base: dir },
  { file: 'nodes-survival.js', base: dir },
  { file: 'nodes-attack.js', base: dir },
  { file: 'skill-params.js', base: dir },
  { file: 'skills/{skill}.js', base: dir },
  { file: 'nodes-objective.js', base: dir },
  { file: 'nodes-movement-v2.js', base: dir },
  { file: 'tree-factory.js', base: dir },
  { file: 'entry.js', base: dir },
];

function parseArgs(argv) {
  var skills = [];
  var writeLegacy = false;

  for (var i = 0; i < argv.length; i++) {
    var arg = argv[i];
    if (arg === '--all-skills') {
      skills = SKILLS.slice();
    } else if (arg === '--legacy') {
      writeLegacy = true;
    } else if (arg === '--skill') {
      if (i + 1 >= argv.length) throw new Error('--skill 需要技能名');
      skills = splitSkills(argv[++i]);
    } else if (arg.indexOf('--skill=') === 0) {
      skills = splitSkills(arg.slice('--skill='.length));
    } else if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error('未知参数: ' + arg);
    }
  }

  if (skills.length === 0) {
    skills = ['teleport'];
    writeLegacy = true;
  }
  validateSkills(skills);
  return { skills: unique(skills), writeLegacy: writeLegacy };
}

function splitSkills(value) {
  return value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
}

function unique(items) {
  var seen = {};
  var out = [];
  for (var i = 0; i < items.length; i++) {
    if (!seen[items[i]]) {
      seen[items[i]] = true;
      out.push(items[i]);
    }
  }
  return out;
}

function validateSkills(skills) {
  for (var i = 0; i < skills.length; i++) {
    if (SKILLS.indexOf(skills[i]) < 0) {
      throw new Error('未知技能: ' + skills[i] + '；可用: ' + SKILLS.join(', '));
    }
  }
}

function printHelp() {
  console.log([
    '用法:',
    '  node build-new.js                         # 构建 teleport 并写回 bt-tank-submit.js',
    '  node build-new.js --skill teleport        # 构建 dist/bt-tank-teleport.js',
    '  node build-new.js --skill shield,boost    # 构建多个指定技能产物',
    '  node build-new.js --all-skills            # 构建 8 个技能产物',
    '  node build-new.js --skill teleport --legacy  # 同时写回 bt-tank-submit.js',
  ].join('\n'));
}

/** 构建期清空 Guard 节点名（体积优化；Guard 名不进 BT_DEBUG trace）。 */
function stripGuardNames(code) {
  return code.replace(/\bGuard\(\s*(['"])(?:\\.|(?!\1).)*?\1\s*,/g, "Guard('',");
}

function buildForSkill(skill) {
  var modules = MODULE_ORDER.map(function (mod) {
    return { file: mod.file.replace('{skill}', skill), base: mod.base };
  });

  var banner = [
    '// ============================================================',
    '// bt-tank-' + skill + '.js — 行为树坦克 AI（自动生成，请勿手动编辑）',
    '// 构建技能: ' + skill,
    '// 源文件: ' + modules.map(function (m) { return m.file; }).join(', '),
    '// 构建时间: ' + new Date().toISOString(),
    '// ============================================================',
    '',
    'const BUILD_SKILL = ' + JSON.stringify(skill) + ';',
    '',
  ].join('\n');

  var body = modules.map(function (mod) {
    var filePath = path.join(mod.base, mod.file);
    if (!fs.existsSync(filePath)) {
      throw new Error('缺少模块文件: ' + filePath);
    }
    var src = fs.readFileSync(filePath, 'utf8');
    return '// ===== ' + mod.file + ' =====\n' + src;
  }).join('\n\n');

  return stripGuardNames(banner + body);
}

function main() {
  var args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(OUT_DIR, { recursive: true });

  for (var i = 0; i < args.skills.length; i++) {
    var skill = args.skills[i];
    var output = buildForSkill(skill);
    var outPath = path.join(OUT_DIR, 'bt-tank-' + skill + '.js');
    fs.writeFileSync(outPath, output, 'utf8');
    console.log('✓ 构建完成 [' + skill + '] → ' + outPath + ' (' + output.length + ' 字节)');

    if (args.writeLegacy && args.skills.length === 1) {
      fs.writeFileSync(LEGACY_OUT, output, 'utf8');
      console.log('✓ 兼容产物 → ' + LEGACY_OUT + ' (' + output.length + ' 字节)');
    }
  }
}

main();
