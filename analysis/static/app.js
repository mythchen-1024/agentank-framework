/* 对局分析仪表盘 */

const charts = {};
let currentRunId = null;
let skillList = [];
let codeHashList = [];

/** 全局筛选：代码版本 + 对手最低 rank_score（传给各 API） */
function filterQuery() {
  const code = document.getElementById("filterCodeHash")?.value || "";
  const minRank = document.getElementById("filterMinRank")?.value || "";
  const parts = [];
  if (code) parts.push(`code_hash=${encodeURIComponent(code)}`);
  if (minRank !== "") parts.push(`min_rank_score=${encodeURIComponent(minRank)}`);
  return parts.length ? `&${parts.join("&")}` : "";
}

function shortHash(h) {
  if (!h || h === "unknown") return "unknown";
  return h.length > 12 ? `${h.slice(0, 12)}…` : h;
}

// 界面中文映射（API 仍用英文 skill/id，仅展示层翻译）
const SKILL_ZH = {
  teleport: "传送",
  shield: "护盾",
  boost: "加速",
  overload: "超载",
  stun: "眩晕",
  freeze: "冰冻",
  cloak: "隐身",
  poison: "中毒",
  unknown: "未知",
};

const TACTIC_ZH = {
  early_star: "开局抢星",
  skill_first: "技能先手",
  point_blank: "贴脸对拼",
  line_control: "守线消耗",
  fast_kill: "速杀",
  star_greed: "抢星送命",
  star_race: "星星落后",
  skill_unused: "未用技能",
  early_crash: "早亡被动",
  aim_fail: "瞄准失误",
  skill_bad_timing: "技能时机差",
  static_target: "站桩靶子",
  over_aggressive: "莽撞对拼",
  dodge_fail: "躲弹失败",
  outscaled: "被运营拖死",
  unknown: "未知",
};

const ENEMY_TAG_ZH = {
  aggressive: "激进",
  static: "站桩",
  defensive: "保守",
  enemy_star_rush: "敌方抢星",
};

const BT_LAYER_ZH = {
  hard_survival: "硬生存缺失",
  soft_survival: "软生存缺失",
  objective: "目标层过强",
  attack: "攻击层过强",
  movement: "移动空转",
  commit: "承诺兑现失败",
  bomb_attack: "炸弹失误",
};

const GAP_ZH = {
  // 与 bt_profile_expectations.json 的 gap_rules 键对应；骨架默认无规则，标签不会出现。
  // 填了 example 里的规则后可在此补中文，未知键直接显示原文。
  should_not_rush_star: "不应抢星",
  should_dodge_more: "应更躲弹",
};

const RESULT_REASON_ZH = {
  star: "星星决胜",
  crashed: "撞毁",
  runTime: "超时运营",
  tie: "平局",
  unknown: "未知",
};

const DIAGNOSIS_ZH = {
  unknown: "未知",
  "did not move": "未移动",
  "too many wall shots": "打墙过多",
  "missed shots": "空枪过多",
  "most shots crashed into walls": "子弹多打墙",
  "no obvious single failure pattern; inspect key events for timing and positioning": "无单一明显失误，需查关键帧",
};

const MAP_ZH = {
  random: "随机",
  classic: "经典",
  grassy_field: "草地",
  desert: "沙漠",
  maze: "迷宫",
};

function skillLabel(key) {
  if (!key) return "-";
  return SKILL_ZH[key] || key;
}

function tacticLabel(key) {
  if (!key) return "未知";
  return TACTIC_ZH[key] || key;
}

function enemyTagLabel(key) {
  if (!key) return key;
  return ENEMY_TAG_ZH[key] || key;
}

function btLayerLabel(key) {
  if (!key) return key;
  return BT_LAYER_ZH[key] || key;
}

function diagnosisLabel(key) {
  if (!key) return "未知";
  const lower = String(key).toLowerCase();
  if (DIAGNOSIS_ZH[lower]) return DIAGNOSIS_ZH[lower];
  if (DIAGNOSIS_ZH[key]) return DIAGNOSIS_ZH[key];
  if (lower.includes("wall")) return "打墙过多";
  if (lower.includes("did not move")) return "未移动";
  if (lower.includes("missed")) return "空枪过多";
  return key.length > 40 ? `${key.slice(0, 38)}…` : key;
}

function parseJsonArray(val) {
  if (!val) return [];
  if (Array.isArray(val)) return val;
  try {
    const arr = JSON.parse(val);
    return Array.isArray(arr) ? arr : [];
  } catch (_) {
    return [];
  }
}

function gapLabel(key) {
  if (!key) return key;
  return GAP_ZH[key] || key;
}

/** 对局明细 💣 列：三态 has_bomb + 我方/敌方放雷数 */
function bombCell(m) {
  if (m.has_bomb == null) {
    return '<span title="无 raw 缓存，无法判定">?</span>';
  }
  if (!m.has_bomb) return "-";
  const parts = [];
  if (m.my_bomb_placed) parts.push(`我${m.my_bomb_placed}`);
  if (m.enemy_bomb_placed) parts.push(`敌${m.enemy_bomb_placed}`);
  const detail = parts.length ? parts.join("/") : String(m.bomb_total ?? "");
  return `💣${detail}`;
}

function resultReasonLabel(key) {
  if (!key) return "-";
  return RESULT_REASON_ZH[key] || key;
}

function formatSuggestParams(obj) {
  if (!obj || typeof obj !== "object") return "-";
  return Object.entries(obj)
    .map(([k, v]) => `${k}: ${v}`)
    .join("; ");
}

function mapLabel(key) {
  if (!key) return "-";
  return MAP_ZH[key] || key;
}

function resultLabel(won, draw) {
  if (draw) return "平";
  return won ? "胜" : "负";
}

function resultTagHtml(won, draw) {
  const text = resultLabel(won, draw);
  if (text === "胜") return '<span class="tag-w">胜</span>';
  if (text === "负") return '<span class="tag-l">负</span>';
  return "平";
}

/** 下拉框：显示中文，value 仍为 API 用的英文 skill */
function fillSkillSelect(selectEl, skills, includeAll) {
  selectEl.innerHTML = "";
  if (includeAll) {
    selectEl.appendChild(new Option("全部技能", ""));
  }
  skills.forEach((sk) => {
    selectEl.appendChild(new Option(skillLabel(sk), sk));
  });
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let msg = await r.text();
    try {
      const j = JSON.parse(msg);
      msg = j.error || msg;
    } catch (_) {}
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return r.json();
}

async function apiDelete(path) {
  const r = await fetch(path, { method: "DELETE" });
  if (!r.ok) {
    let msg = await r.text();
    try {
      const j = JSON.parse(msg);
      msg = j.error || msg;
    } catch (_) {}
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return r.json();
}

function wrColor(rate) {
  if (rate >= 60) return "rgba(34, 197, 94, 0.85)";
  if (rate >= 45) return "rgba(234, 179, 8, 0.85)";
  return "rgba(239, 68, 68, 0.85)";
}

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
}

/** 无数据时在 canvas 父容器显示提示，避免空白图表 */
function showChartEmpty(canvasId, message) {
  destroyChart(canvasId);
  const wrap = document.getElementById(canvasId)?.parentElement;
  if (!wrap) return;
  let hint = wrap.querySelector(".chart-empty");
  if (!hint) {
    hint = document.createElement("div");
    hint.className = "chart-empty";
    wrap.appendChild(hint);
  }
  hint.textContent = message;
  hint.hidden = false;
}

function hideChartEmpty(canvasId) {
  const wrap = document.getElementById(canvasId)?.parentElement;
  const hint = wrap?.querySelector(".chart-empty");
  if (hint) hint.hidden = true;
}

async function checkServerVersion() {
  const banner = document.getElementById("serverBanner");
  const btnDelete = document.getElementById("btnDeleteRun");
  if (!banner) return;
  try {
    const health = await api("/api/health");
    if ((health.version || 0) < 6) {
      banner.hidden = false;
      banner.textContent =
        "检测到旧版 report_server（缺少 timeline / profile-gaps 等 API）。请在 all-round-tank 目录重启：python report_server.py --db analysis/data.db --port 8765";
      if (btnDelete) btnDelete.title = "需重启 report_server 后才可删除";
    } else {
      banner.hidden = true;
      if (btnDelete && (health.version || 0) < 4) {
        btnDelete.title = "删除功能需重启 report_server 加载新版后可用";
      }
    }
  } catch (_) {
    banner.hidden = false;
    banner.textContent = "无法连接 /api/health，请确认 report_server 已启动。";
  }
}

async function loadRuns() {
  const runs = await api("/api/runs");
  const sel = document.getElementById("runSelect");
  const btnDelete = document.getElementById("btnDeleteRun");
  sel.innerHTML = "";
  if (!runs.length) {
    currentRunId = null;
    sel.innerHTML = "<option>无数据 — 请先运行 match_analyzer.py</option>";
    if (btnDelete) btnDelete.disabled = true;
    return false;
  }
  runs.forEach((r) => {
    const total = r.match_count || 0;
    const wins = r.wins || 0;
    const wr = total ? ((wins / total) * 100).toFixed(1) : "0";
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `#${r.id} ${r.run_at?.slice(0, 19) || ""} — ${total}场 胜率${wr}%`;
    sel.appendChild(opt);
  });
  currentRunId = runs[0].id;
  sel.value = currentRunId;
  if (btnDelete) btnDelete.disabled = false;
  return true;
}

async function deleteCurrentRun() {
  if (!currentRunId) return;

  try {
    const health = await api("/api/health");
    if ((health.version || 0) < 4 || !health.delete_run) {
      alert("当前 report_server 不支持删除，请重启服务后再试：\npython report_server.py --db analysis/data.db --port 8765");
      return;
    }
  } catch (_) {
    alert("无法连接服务器，请确认 report_server 已启动。");
    return;
  }

  const sel = document.getElementById("runSelect");
  const label = sel.options[sel.selectedIndex]?.textContent || `#${currentRunId}`;
  const ok = confirm(
    `确定删除分析批次 ${label}？\n\n仅移除此批次；若某对局不被其他批次引用，会一并从库中清除。`
  );
  if (!ok) return;

  const btn = document.getElementById("btnDeleteRun");
  btn.disabled = true;
  try {
    await apiDelete(`/api/runs/${currentRunId}`);
    const hasRuns = await loadRuns();
    if (hasRuns) {
      await refreshAll();
    } else {
      document.getElementById("kpiTotal").textContent = "-";
      document.getElementById("kpiW").textContent = "-";
      document.getElementById("kpiL").textContent = "-";
      document.getElementById("kpiWr").textContent = "-";
      Object.keys(charts).forEach((id) => destroyChart(id));
      document.querySelector("#matchTable tbody").innerHTML =
        '<tr><td colspan="14" style="color:var(--muted)">无数据</td></tr>';
      document.querySelector("#diagnosisTable tbody").innerHTML =
        '<tr><td colspan="3" style="color:var(--muted)">无数据</td></tr>';
    }
  } catch (e) {
    const msg = String(e.message || e);
    if (msg.includes("501") || msg.includes("Unsupported method")) {
      alert("删除失败：服务端未支持 DELETE。请重启 report_server 后再试。");
    } else {
      alert(`删除失败：${msg}`);
    }
    btn.disabled = !currentRunId;
  }
}

async function loadCodeHashes() {
  if (!currentRunId) return;
  try {
    const rows = await api(`/api/runs/${currentRunId}/code-hashes`);
    codeHashList = rows || [];
  } catch (e) {
    // 旧版 report_server 无 /code-hashes 时不阻断 overview 等基础统计
    console.warn("code-hashes 不可用（需 report_server v5+）:", e.message);
    codeHashList = [];
    return;
  }
  const sel = document.getElementById("filterCodeHash");
  const compareA = document.getElementById("compareHashA");
  const compareB = document.getElementById("compareHashB");
  const prev = sel?.value || "";
  if (sel) {
    sel.innerHTML = '<option value="">全部版本</option>';
    codeHashList.forEach((r) => {
      const total = r.total || 0;
      const wins = r.wins || 0;
      const wr = total ? ((wins / total) * 100).toFixed(0) : 0;
      const label = `${shortHash(r.code_hash)} (${wins}/${total} WR${wr}%)`;
      sel.appendChild(new Option(label, r.code_hash));
    });
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  const fillCompare = (el, placeholder) => {
    if (!el) return;
    el.innerHTML = `<option value="">${placeholder}</option>`;
    codeHashList.forEach((r) => {
      el.appendChild(new Option(shortHash(r.code_hash), r.code_hash));
    });
  };
  fillCompare(compareA, "版本 A");
  fillCompare(compareB, "版本 B");
  if (codeHashList.length >= 2) {
    compareA.value = codeHashList[1].code_hash;
    compareB.value = codeHashList[0].code_hash;
  }
}

async function loadOverview() {
  const data = await api(`/api/runs/${currentRunId}/overview${filterQuery().replace(/^&/, "?")}`);
  const ov = data.overall || {};
  const total = ov.total || 0;
  const wins = ov.wins || 0;
  const losses = ov.losses || 0;
  document.getElementById("kpiTotal").textContent = total;
  document.getElementById("kpiW").textContent = wins;
  document.getElementById("kpiL").textContent = losses;
  document.getElementById("kpiWr").textContent = total ? `${((wins / total) * 100).toFixed(1)}%` : "-";

  skillList = (data.by_skill || []).map((s) => s.my_skill);
  const skillSel = document.getElementById("behaviorSkill");
  const filterSkill = document.getElementById("filterSkill");
  fillSkillSelect(skillSel, skillList, false);
  fillSkillSelect(filterSkill, skillList, true);
  if (skillList.length) skillSel.value = skillList[0];

  const matrixMy = document.getElementById("matrixMySkill");
  const matrixEnemy = document.getElementById("matrixEnemySkill");
  fillSkillSelect(matrixMy, skillList, true);
  fillSkillSelect(matrixEnemy, [], true);
}

async function loadSkillMatrix() {
  const fq = filterQuery().replace(/^&/, "?");
  const rows = await api(`/api/runs/${currentRunId}/skill-matrix${fq ? fq : ""}`);
  const mySkills = [...new Set(rows.map((r) => r.my_skill))].sort();
  const enemySkills = [...new Set(rows.map((r) => r.enemy_skill))].sort();
  const lookup = {};
  rows.forEach((r) => {
    lookup[`${r.my_skill}|${r.enemy_skill}`] = r;
  });

  // 对局明细的敌技能筛选下拉复用矩阵里出现过的 enemy_skill 集合
  const filterEnemy = document.getElementById("filterEnemySkill");
  if (filterEnemy) {
    const prev = filterEnemy.value;
    filterEnemy.innerHTML = '<option value="">全部敌技能</option>';
    enemySkills.forEach((es) => filterEnemy.appendChild(new Option(skillLabel(es), es)));
    if (prev && [...filterEnemy.options].some((o) => o.value === prev)) filterEnemy.value = prev;
  }

  destroyChart("matrixChart");
  const datasets = enemySkills.map((es) => ({
    label: skillLabel(es),
    data: mySkills.map((ms) => {
      const cell = lookup[`${ms}|${es}`];
      return cell ? (cell.total ? (cell.wins / cell.total) * 100 : 0) : null;
    }),
    backgroundColor: mySkills.map((ms) => {
      const cell = lookup[`${ms}|${es}`];
      const wr = cell && cell.total ? (cell.wins / cell.total) * 100 : 0;
      return wrColor(wr);
    }),
  }));

  charts.matrixChart = new Chart(document.getElementById("matrixChart"), {
    type: "bar",
    data: { labels: mySkills.map(skillLabel), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 12 } },
        tooltip: {
          callbacks: {
            afterLabel(ctx) {
              const es = enemySkills[ctx.datasetIndex];
              const ms = mySkills[ctx.dataIndex];
              const cell = lookup[`${ms}|${es}`];
              if (!cell) return "";
              return `胜${cell.wins} / 负${cell.losses} / 共${cell.total}场`;
            },
          },
        },
      },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: "胜率（%）" } },
        x: { title: { display: true, text: "我方技能" } },
      },
    },
  });

  let legendHtml = rows
    .slice(0, 12)
    .map((r) => {
      const wr = r.total ? ((r.wins / r.total) * 100).toFixed(0) : 0;
      return `<span>${skillLabel(r.my_skill)} 对 ${skillLabel(r.enemy_skill)}：${wr}%（${r.wins}胜/${r.total}场）</span>`;
    })
    .join(" · ");
  document.getElementById("matrixLegend").innerHTML = legendHtml || "暂无矩阵数据";
}

async function loadOpponents() {
  const sort = document.getElementById("oppSort").value === "frequent" ? "frequent" : "worst";
  const minSamples = parseInt(document.getElementById("oppMinSamples").value, 10) || 2;
  const data = await api(
    `/api/runs/${currentRunId}/opponents?sort=${sort}&min_samples=${minSamples}&page_size=15${filterQuery()}`
  );
  const items = (data.items || []).slice(0, 15).reverse();

  destroyChart("opponentChart");
  charts.opponentChart = new Chart(document.getElementById("opponentChart"), {
    type: "bar",
    data: {
      labels: items.map((o) => o.opponent_name?.slice(0, 16) || "?"),
      datasets: [
        {
          label: "胜率 %",
          data: items.map((o) => o.win_rate),
          backgroundColor: items.map((o) => wrColor(o.win_rate)),
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          min: 0,
          max: 100,
          title: { display: true, text: "胜率（%）" },
        },
      },
    },
  });
}

async function loadBehavior() {
  const skill = document.getElementById("behaviorSkill").value;
  if (!skill) return;
  const rows = await api(
    `/api/runs/${currentRunId}/behavior?my_skill=${encodeURIComponent(skill)}${filterQuery()}`
  );
  const win = rows.find((r) => r.result === "W") || {};
  const loss = rows.find((r) => r.result === "L") || {};
  const metrics = [
    { key: "avg_shots_fired", label: "开火" },
    { key: "avg_shots_hit", label: "命中" },
    { key: "avg_moves", label: "移动" },
    { key: "avg_stars", label: "星星" },
    { key: "avg_skill_used", label: "技能" },
  ];

  destroyChart("behaviorChart");
  charts.behaviorChart = new Chart(document.getElementById("behaviorChart"), {
    type: "bar",
    data: {
      labels: metrics.map((m) => m.label),
      datasets: [
        { label: "胜", data: metrics.map((m) => win[m.key] || 0), backgroundColor: "rgba(34,197,94,0.7)" },
        { label: "负", data: metrics.map((m) => loss[m.key] || 0), backgroundColor: "rgba(239,68,68,0.7)" },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "top" } },
    },
  });
}

async function loadEnemyProfiles() {
  const winRows = await api(`/api/runs/${currentRunId}/enemy-profiles?result=W${filterQuery().replace(/^&/, "&")}`);
  const lossRows = await api(`/api/runs/${currentRunId}/enemy-profiles?result=L${filterQuery().replace(/^&/, "&")}`);
  const skills = [...new Set([...winRows, ...lossRows].map((r) => r.enemy_skill))];

  destroyChart("enemyChart");
  charts.enemyChart = new Chart(document.getElementById("enemyChart"), {
    type: "bar",
    data: {
      labels: skills.map(skillLabel),
      datasets: [
        {
          label: "我胜时 · 敌方开火",
          data: skills.map((sk) => (winRows.find((r) => r.enemy_skill === sk) || {}).avg_shots || 0),
          backgroundColor: "rgba(34,197,94,0.6)",
        },
        {
          label: "我负时 · 敌方开火",
          data: skills.map((sk) => (lossRows.find((r) => r.enemy_skill === sk) || {}).avg_shots || 0),
          backgroundColor: "rgba(239,68,68,0.6)",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "top" } },
    },
  });
}

async function loadWinPatterns() {
  const rows = await api(`/api/runs/${currentRunId}/win-patterns${filterQuery().replace(/^&/, "?")}`);
  const grouped = {};
  rows.forEach((r) => {
    grouped[r.tactic] = (grouped[r.tactic] || 0) + r.n;
  });
  const keys = Object.keys(grouped);
  const labels = keys.map(tacticLabel);
  const values = keys.map((k) => grouped[k]);

  destroyChart("winPatternChart");
  charts.winPatternChart = new Chart(document.getElementById("winPatternChart"), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: ["#3b82f6", "#22c55e", "#eab308", "#a855f7", "#64748b"] }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "right" } },
    },
  });
}

async function loadLossPatterns() {
  const rows = await api(`/api/runs/${currentRunId}/loss-patterns${filterQuery().replace(/^&/, "?")}`);
  const grouped = {};
  rows.forEach((r) => {
    grouped[r.tactic] = (grouped[r.tactic] || 0) + r.n;
  });
  const keys = Object.keys(grouped);
  if (!keys.length) {
    showChartEmpty("lossPatternChart", "暂无负场战术数据（需有负场且拉取了 events）");
    return;
  }
  hideChartEmpty("lossPatternChart");
  const labels = keys.map(tacticLabel);
  const values = keys.map((k) => grouped[k]);

  destroyChart("lossPatternChart");
  charts.lossPatternChart = new Chart(document.getElementById("lossPatternChart"), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: ["#ef4444", "#f97316", "#eab308", "#a855f7", "#64748b", "#06b6d4"] }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "right" } },
    },
  });
}

async function loadCrossMatrix() {
  const data = await api(`/api/runs/${currentRunId}/cross-matrix?result=L${filterQuery()}`);
  const cells = data.cells || [];
  const myFilter = document.getElementById("matrixMySkill").value;
  const enemyFilter = document.getElementById("matrixEnemySkill").value;

  const enemySkills = [...new Set(cells.map((c) => c.enemy_skill))].sort();
  const matrixEnemy = document.getElementById("matrixEnemySkill");
  if (matrixEnemy.options.length <= 1 && enemySkills.length) {
    fillSkillSelect(matrixEnemy, enemySkills, true);
  }

  let filtered = cells;
  if (myFilter) filtered = filtered.filter((c) => c.my_skill === myFilter);
  if (enemyFilter) filtered = filtered.filter((c) => c.enemy_skill === enemyFilter);

  const agg = {};
  filtered.forEach((c) => {
    const key = `${c.enemy_tag}|${c.tactic}`;
    agg[key] = (agg[key] || 0) + c.n;
  });
  const top = Object.entries(agg)
    .map(([k, n]) => {
      const [enemy_tag, tactic] = k.split("|");
      return { enemy_tag, tactic, n };
    })
    .sort((a, b) => b.n - a.n)
    .slice(0, 12);

  if (!top.length) {
    showChartEmpty("crossMatrixChart", "暂无交叉矩阵数据（负场需有 enemy_playstyle_tags）");
    return;
  }
  hideChartEmpty("crossMatrixChart");

  destroyChart("crossMatrixChart");
  charts.crossMatrixChart = new Chart(document.getElementById("crossMatrixChart"), {
    type: "bar",
    data: {
      labels: top.map((r) => `${enemyTagLabel(r.enemy_tag)} · ${tacticLabel(r.tactic)}`),
      datasets: [{ label: "负场次数", data: top.map((r) => r.n), backgroundColor: "rgba(239,68,68,0.7)" }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
    },
  });
}

async function loadDiagnosis() {
  const data = await api(`/api/runs/${currentRunId}/diagnosis?result=L${filterQuery()}`);
  const items = (data.items || []).slice(0, 15);
  const tbody = document.querySelector("#diagnosisTable tbody");
  tbody.innerHTML = "";
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--muted)">暂无数据</td></tr>';
    return;
  }
  items.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${diagnosisLabel(row.diagnosis)}</td>
      <td>${skillLabel(row.enemy_skill)}</td>
      <td>${row.n}</td>`;
    tbody.appendChild(tr);
  });
}

async function loadBtLayers() {
  const rows = await api(`/api/runs/${currentRunId}/bt-layer-losses${filterQuery().replace(/^&/, "?")}`);
  if (!rows.length) {
    showChartEmpty("btLayerChart", "暂无 BT 层级归因数据（负场需 events 或 raw 遥测）");
    return;
  }
  hideChartEmpty("btLayerChart");
  const skills = [...new Set(rows.map((r) => r.my_skill))].sort();
  const layerKeys = [...new Set(rows.map((r) => `${r.layer}|${r.source}`))].sort();

  const datasets = layerKeys.map((key, i) => {
    const [layer, source] = key.split("|");
    const colors = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#a855f7", "#64748b"];
    const suffix = source === "real" ? " (实证)" : " (推断)";
    return {
      label: btLayerLabel(layer) + suffix,
      data: skills.map((sk) => {
        const cell = rows.find((r) => r.my_skill === sk && r.layer === layer && r.source === source);
        return cell ? cell.n : 0;
      }),
      backgroundColor: colors[i % colors.length],
    };
  });

  destroyChart("btLayerChart");
  charts.btLayerChart = new Chart(document.getElementById("btLayerChart"), {
    type: "bar",
    data: { labels: skills.map(skillLabel), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
      scales: { x: { stacked: true }, y: { stacked: true, title: { display: true, text: "负场次数" } } },
    },
  });
}

async function loadResultReasons() {
  const rows = await api(`/api/runs/${currentRunId}/result-reasons${filterQuery().replace(/^&/, "?")}`);
  if (!rows.length) {
    showChartEmpty("resultReasonChart", "暂无负场终局原因数据");
    return;
  }
  hideChartEmpty("resultReasonChart");
  destroyChart("resultReasonChart");
  charts.resultReasonChart = new Chart(document.getElementById("resultReasonChart"), {
    type: "doughnut",
    data: {
      labels: rows.map((r) => resultReasonLabel(r.reason)),
      datasets: [{
        data: rows.map((r) => r.n),
        backgroundColor: ["#ef4444", "#f97316", "#eab308", "#64748b"],
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "right" } },
    },
  });
}

async function loadProfileGaps() {
  const rows = await api(`/api/runs/${currentRunId}/profile-gaps${filterQuery().replace(/^&/, "?")}`);
  const tbody = document.querySelector("#profileGapsTable tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="color:var(--muted)">暂无 Gap 标签（负场需匹配 gap_rules）</td></tr>';
    return;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${gapLabel(row.gap_tag)}</td>
      <td><small>${formatSuggestParams(row.suggest_params)}</small></td>
      <td>${row.n}</td>`;
    tbody.appendChild(tr);
  });
}

let activeReplayViewer = null;

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** 地图帧查看器：只读本地 raw 缓存；404 时显示 reanalyze 提示，不阻断 timeline。返回 raw 数据或 null */
async function initReplayViewer(matchUrlId) {
  const root = document.getElementById("replayViewerRoot");
  if (!root) return null;
  if (activeReplayViewer) {
    activeReplayViewer.destroy();
    activeReplayViewer = null;
  }
  root.innerHTML = '<p class="rv-hint">地图加载中…</p>';
  try {
    const data = await api(`/api/matches/${encodeURIComponent(matchUrlId)}/replay-raw`);
    root.innerHTML = "";
    activeReplayViewer = new ReplayViewer(root, data);
    // 帧变化 → 高亮 timeline 中该帧（若存在）并滚动到可见
    activeReplayViewer.onFrameChange = (frame) => {
      document.querySelectorAll("#timelineModalBody .timeline-row").forEach((row) => {
        const match = row.dataset.frame === String(frame);
        row.classList.toggle("timeline-current", match);
        if (match) row.scrollIntoView({ block: "nearest" });
      });
    };
    if (data.anchor_frame != null) activeReplayViewer.seek(data.anchor_frame);
    return data;
  } catch (e) {
    root.innerHTML = `<p class="rv-hint">无地图数据：${e.message === "no_raw_cache"
      ? "本地无 raw 缓存，可执行 python match_analyzer.py --reanalyze-run 该批次id 补拉"
      : e.message}</p>`;
    return null;
  }
}

/** timeline 行点击 → 地图跳帧 */
function bindTimelineRowClicks(body) {
  body.querySelectorAll(".timeline-row").forEach((row) => {
    row.addEventListener("click", () => {
      if (activeReplayViewer) activeReplayViewer.seek(parseInt(row.dataset.frame, 10));
    });
  });
}

/**
 * 有 raw 缓存时：从重建帧（Event）+ 全程 print 日志（Print/Profile）渲染完整 timeline（f0 起）。
 * DB 的 match_key_events / behavior_log 只存终局窗口（锚点前 30 帧），全程视图必须走 raw。
 */
function renderFullTimeline(body, rawData, anchorFrame) {
  const frames = activeReplayViewer ? activeReplayViewer.frames : [];
  const logsByFrame = {};
  (rawData.logs || []).forEach((l) => {
    if (!logsByFrame[l.frame]) logsByFrame[l.frame] = [];
    logsByFrame[l.frame].push(l);
  });
  // 双方都有日志时才加名字前缀（通常只有我方开了 print）
  const multiPlayerLogs = new Set((rawData.logs || []).map((l) => l.player)).size > 1;
  const names = rawData.names || [];
  const rowsHtml = [];
  for (let f = 0; f < frames.length; f++) {
    const evItems = frames[f].notes || [];
    const printItems = [];
    const pfItems = [];
    (logsByFrame[f] || []).forEach((l) => {
      const text = multiPlayerLogs ? `${names[l.player] || `P${l.player}`}: ${l.text}` : l.text;
      if (l.text.startsWith("PF ")) pfItems.push(text);
      else printItems.push(text);
    });
    if (!evItems.length && !printItems.length && !pfItems.length && f !== anchorFrame) continue;
    const anchorMark = f === anchorFrame ? " timeline-anchor" : "";
    const lane = (items, label, cls) => (items.length
      ? `<div class="tl-lane ${cls}"><span class="tl-lane-label">${label}</span>${items.map((t) => `<span class="tl-item">${escapeHtml(t)}</span>`).join("")}</div>`
      : `<div class="tl-lane ${cls}"><span class="tl-lane-label">${label}</span><span class="tl-empty">-</span></div>`);
    rowsHtml.push(`<div class="timeline-row${anchorMark}" data-frame="${f}">
      <div class="tl-frame" title="点击跳到该帧地图">f${f}</div>
      <div class="tl-lanes">
        ${lane(evItems, "Event", "tl-event")}
        ${lane(printItems, "Print", "tl-behavior")}
        ${lane(pfItems, "Profile", "tl-profile")}
      </div>
    </div>`);
  }
  body.innerHTML = rowsHtml.join("") || '<p style="color:var(--muted)">无事件与日志</p>';
  bindTimelineRowClicks(body);
  // 重建 body 后刷新当前帧高亮
  if (activeReplayViewer) activeReplayViewer.seek(activeReplayViewer.idx);
}

async function openTimelineModal(matchUrlId) {
  const modal = document.getElementById("timelineModal");
  const body = document.getElementById("timelineModalBody");
  const meta = document.getElementById("timelineModalMeta");
  const title = document.getElementById("timelineModalTitle");
  const replay = document.getElementById("timelineModalReplay");
  modal.hidden = false;
  body.innerHTML = '<p style="color:var(--muted)">加载中…</p>';
  meta.textContent = "";
  // 地图查看器与 timeline 并行加载，互不阻断
  const viewerPromise = initReplayViewer(matchUrlId);
  try {
    const data = await api(`/api/matches/${encodeURIComponent(matchUrlId)}/timeline`);
    title.textContent = `负场时序 · ${matchUrlId}`;
    const layers = (data.real_bt_layers || []).map(btLayerLabel).join("、") || "-";
    const dom = data.dominant_bt_layer ? btLayerLabel(data.dominant_bt_layer) : "-";
    meta.innerHTML = `
      <span>终局: ${resultReasonLabel(data.result_reason)}</span>
      <span>战术: ${tacticLabel(data.my_loss_tactic)}</span>
      <span>锚点 f${data.anchor_frame ?? "-"}</span>
      <span>BT: ${layers}（主导 ${dom}）</span>
      <span>星差: ${data.star_delta ?? "-"}</span>`;
    if (data.history_url) {
      replay.href = data.history_url;
      replay.hidden = false;
    } else {
      replay.hidden = true;
    }
    // 有 raw 缓存 → 全程 timeline（f0 起）；无 raw → 回退 DB 终局窗口
    const rawData = await viewerPromise;
    if (rawData) {
      renderFullTimeline(body, rawData, data.anchor_frame ?? rawData.anchor_frame);
      return;
    }
    const frames = [...new Set((data.items || []).map((i) => i.frame))].sort((a, b) => a - b);
    if (!frames.length) {
      body.innerHTML = '<p style="color:var(--muted)">无终局窗口数据（需 reanalyze 负场）</p>';
      return;
    }
    const byFrame = {};
    (data.items || []).forEach((item) => {
      if (!byFrame[item.frame]) byFrame[item.frame] = { event: [], behavior: [], profile: [] };
      byFrame[item.frame][item.lane]?.push(item);
    });
    body.innerHTML = frames.map((fr) => {
      const bucket = byFrame[fr] || {};
      const anchorMark = fr === data.anchor_frame ? " timeline-anchor" : "";
      const renderLane = (lane, label, cls) => {
        const items = bucket[lane] || [];
        if (!items.length) return `<div class="tl-lane ${cls}"><span class="tl-lane-label">${label}</span><span class="tl-empty">-</span></div>`;
        return `<div class="tl-lane ${cls}"><span class="tl-lane-label">${label}</span>${items.map((i) => `<span class="tl-item">${i.label}</span>`).join("")}</div>`;
      };
      return `<div class="timeline-row${anchorMark}" data-frame="${fr}">
        <div class="tl-frame" title="点击跳到该帧地图">f${fr}</div>
        <div class="tl-lanes">
          ${renderLane("event", "Event", "tl-event")}
          ${renderLane("behavior", "Print", "tl-behavior")}
          ${renderLane("profile", "Profile", "tl-profile")}
        </div>
      </div>`;
    }).join("");
    bindTimelineRowClicks(body);
  } catch (e) {
    body.innerHTML = `<p style="color:var(--loss)">加载失败: ${e.message}</p>`;
  }
}

function closeTimelineModal() {
  document.getElementById("timelineModal").hidden = true;
  if (activeReplayViewer) {
    activeReplayViewer.destroy();
    activeReplayViewer = null;
  }
}

async function loadMatches() {
  const skill = document.getElementById("filterSkill").value;
  const wonRaw = document.getElementById("filterResult").value;
  const opponent = document.getElementById("filterOpponent").value;
  const enemySkill = document.getElementById("filterEnemySkill")?.value || "";
  const resultReason = document.getElementById("filterResultReason")?.value || "";
  const lossTactic = document.getElementById("filterLossTactic")?.value || "";
  const gapTag = document.getElementById("filterGapTag")?.value.trim() || "";
  const matchId = document.getElementById("filterMatchId")?.value.trim() || "";
  const hasBomb = document.getElementById("filterHasBomb")?.value || "";
  const errEl = document.getElementById("matchLoadError");
  let url = `/api/matches?run_id=${currentRunId}&page_size=50${filterQuery()}`;
  if (skill) url += `&my_skill=${encodeURIComponent(skill)}`;
  if (wonRaw !== "") url += `&won=${wonRaw}`;
  if (opponent) url += `&opponent=${encodeURIComponent(opponent)}`;
  if (enemySkill) url += `&enemy_skill=${encodeURIComponent(enemySkill)}`;
  if (resultReason) url += `&result_reason=${encodeURIComponent(resultReason)}`;
  if (lossTactic) url += `&loss_tactic=${encodeURIComponent(lossTactic)}`;
  if (gapTag) url += `&gap_tag=${encodeURIComponent(gapTag)}`;
  if (matchId) url += `&match_id=${encodeURIComponent(matchId)}`;
  if (hasBomb) url += `&has_bomb=${encodeURIComponent(hasBomb)}`;

  const tbody = document.querySelector("#matchTable tbody");
  try {
    errEl.hidden = true;
    errEl.textContent = "";
    const rows = await api(url);
    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="14" style="color:var(--muted)">无匹配对局</td></tr>';
      return;
    }
    rows.forEach((m) => {
      const tr = document.createElement("tr");
      const tag = resultTagHtml(m.won, m.draw);
      const link = m.history_url
        ? `<a href="${m.history_url}" target="_blank" rel="noopener">回放</a>`
        : "-";
      const lossTactic = m.won || m.draw ? "-" : tacticLabel(m.my_loss_tactic);
      const realLayers = parseJsonArray(m.real_bt_layers);
      const inferredLayers = parseJsonArray(m.inferred_bt_layers);
      const layers = (m.has_raw && realLayers.length ? realLayers : inferredLayers)
        .map(btLayerLabel).join("、") || "-";
      const domHint = m.dominant_bt_layer ? ` <small>(${btLayerLabel(m.dominant_bt_layer)})</small>` : "";
      const layerTag = m.has_raw && realLayers.length ? "实证" : (inferredLayers.length ? "推断" : "");
      const ver = shortHash(m.my_code_hash);
      // 对局 ID 完整展示，便于对照筛选框与外部回放链接
      const mid = m.match_url_id || "-";
      const midCell = mid === "-"
        ? "-"
        : `<code class="match-id" title="${mid}">${mid}</code>`;
      const meStars = m.me_stars;
      const enStars = m.enemy_stars_stat ?? m.enemy_stars;
      const starCell = m.won || m.draw ? "-" : `${meStars ?? "-"}:${enStars ?? "-"}`;
      const endReason = m.won || m.draw ? "-" : resultReasonLabel(m.result_reason);
      const gaps = parseJsonArray(m.profile_gap_tags).map(gapLabel).join("、") || "-";
      const summary = parseJsonArray(m.critical_events_summary);
      let keyEv = "-";
      if (!m.won && !m.draw && summary.length) {
        keyEv = `<button type="button" class="link-btn timeline-btn" data-match="${m.match_url_id}">${summary.slice(0, 3).join(" ")}</button>`;
      } else if (!m.won && !m.draw && m.has_timeline) {
        keyEv = `<button type="button" class="link-btn timeline-btn" data-match="${m.match_url_id}">查看</button>`;
      }
      tr.innerHTML = `
      <td>${tag}</td>
      <td>${skillLabel(m.my_skill)}</td>
      <td>${m.opponent_name || ""}</td>
      <td>${skillLabel(m.enemy_skill)}</td>
      <td>${bombCell(m)}</td>
      <td>${endReason}</td>
      <td>${starCell}</td>
      <td>${lossTactic}</td>
      <td>${layers}${domHint}${layerTag ? ` <small>(${layerTag})</small>` : ""}</td>
      <td>${keyEv}</td>
      <td><small>${gaps}</small></td>
      <td>${ver}</td>
      <td>${midCell}</td>
      <td>${link}</td>`;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll(".timeline-btn").forEach((btn) => {
      btn.addEventListener("click", () => openTimelineModal(btn.dataset.match));
    });
  } catch (e) {
    console.error("loadMatches failed:", e);
    tbody.innerHTML = "";
    errEl.hidden = false;
    errEl.textContent = `对局明细加载失败：${e.message}。请在该目录重启：python report_server.py --db analysis/data.db`;
  }
}

async function loadVersionCompare() {
  const hashA = document.getElementById("compareHashA")?.value;
  const hashB = document.getElementById("compareHashB")?.value;
  const tbody = document.querySelector("#versionCompareTable tbody");
  if (!hashA || !hashB || hashA === hashB) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">请选择两个不同版本</td></tr>';
    return;
  }
  const minRank = document.getElementById("filterMinRank")?.value;
  let url = `/api/runs/${currentRunId}/version-compare?hash_a=${encodeURIComponent(hashA)}&hash_b=${encodeURIComponent(hashB)}`;
  if (minRank !== "") url += `&min_rank_score=${encodeURIComponent(minRank)}`;
  const data = await api(url);
  const rows = data.rows || [];
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">无对比数据</td></tr>';
    return;
  }
  rows.forEach((r) => {
    const delta = r.delta_wr != null ? `${r.delta_wr > 0 ? "+" : ""}${r.delta_wr}%` : "-";
    const deltaClass = r.delta_wr > 0 ? "tag-w" : r.delta_wr < 0 ? "tag-l" : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${skillLabel(r.enemy_skill)}</td>
      <td>${r.a_wr != null ? `${r.a_wr}% (${r.a_wins}/${r.a_total})` : "-"}</td>
      <td>${r.b_wr != null ? `${r.b_wr}% (${r.b_wins}/${r.b_total})` : "-"}</td>
      <td><span class="${deltaClass}">${delta}</span></td>
      <td>${r.a_total}</td>
      <td>${r.b_total}</td>`;
    tbody.appendChild(tr);
  });
}

async function refreshAll() {
  if (!currentRunId) return;
  await checkServerVersion();
  // code-hashes / version-compare 为 v5 能力；失败时仍加载 overview 等基础模块
  try { await loadCodeHashes(); } catch (e) { console.warn(e); }
  await loadOverview();
  const tasks = [
    loadSkillMatrix(),
    loadOpponents(),
    loadBehavior(),
    loadEnemyProfiles(),
    loadWinPatterns(),
    loadLossPatterns(),
    loadCrossMatrix(),
    loadDiagnosis(),
    loadResultReasons(),
    loadProfileGaps(),
    loadBtLayers(),
    loadMatches(),
    loadVersionCompare(),
  ];
  const results = await Promise.allSettled(tasks);
  const failed = results.filter((r) => r.status === "rejected");
  if (failed.length) {
    console.warn("部分模块加载失败", failed);
  }
}

document.getElementById("runSelect").addEventListener("change", (e) => {
  currentRunId = parseInt(e.target.value, 10);
  refreshAll();
});

document.getElementById("btnRefresh").addEventListener("click", refreshAll);
document.getElementById("btnDeleteRun").addEventListener("click", deleteCurrentRun);
document.getElementById("behaviorSkill").addEventListener("change", loadBehavior);
document.getElementById("oppSort").addEventListener("change", loadOpponents);
document.getElementById("oppMinSamples").addEventListener("change", loadOpponents);
document.getElementById("btnFilter").addEventListener("click", loadMatches);
document.getElementById("filterCodeHash").addEventListener("change", refreshAll);
document.getElementById("filterMinRank").addEventListener("change", refreshAll);
document.getElementById("btnCompare").addEventListener("click", loadVersionCompare);
document.getElementById("matrixMySkill").addEventListener("change", loadCrossMatrix);
document.getElementById("matrixEnemySkill").addEventListener("change", loadCrossMatrix);
document.getElementById("timelineModalClose").addEventListener("click", closeTimelineModal);
document.getElementById("timelineModalBackdrop").addEventListener("click", closeTimelineModal);

// 负场战术筛选下拉：固定 key 列表（与 README 负场战术标签一致），显示中文
(() => {
  const sel = document.getElementById("filterLossTactic");
  if (!sel) return;
  const lossKeys = [
    "star_greed", "star_race", "skill_unused", "early_crash", "aim_fail",
    "skill_bad_timing", "static_target", "over_aggressive", "dodge_fail", "outscaled",
  ];
  lossKeys.forEach((k) => sel.appendChild(new Option(tacticLabel(k), k)));
})();

(async () => {
  try {
    const hasRuns = await loadRuns();
    if (hasRuns) await refreshAll();
  } catch (e) {
    console.error(e);
    document.body.insertAdjacentHTML(
      "beforeend",
      `<p style="padding:1rem;color:red">仪表盘初始化失败: ${e.message}（请先运行 python report_server.py）</p>`
    );
  }
})();
