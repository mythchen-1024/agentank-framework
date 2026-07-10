/*
 * 地图帧回放查看器
 * 从 /api/matches/{id}/replay-raw 返回的 records 重建每帧实体状态（坦克/子弹/星星/炸弹），
 * 渲染到 Canvas，支持手动逐帧前进/后退、slider 拖动与键盘控制。
 * 帧重建逻辑移植自 agentank-simulator/tools/replay-summary.mjs 的 reconstruct()。
 */

(() => {
  const DIRS = ["up", "right", "down", "left"];
  // 与 agentank-simulator/src/constants.js 对齐，viewer 不依赖 simulator 包
  const BOMB_FUSE_FRAMES = 10;
  const BOMB_BLAST_RANGE = 2;
  const DIR_DELTA = {
    up: [0, -1],
    right: [1, 0],
    down: [0, 1],
    left: [-1, 0],
  };

  /** turn 事件的 direction 是 left/right 相对转向 */
  function rotDir(dir, turn) {
    const i = DIRS.indexOf(dir);
    if (i < 0) return dir;
    if (turn === "left") return DIRS[(i + 3) % 4];
    if (turn === "right") return DIRS[(i + 1) % 4];
    return turn || dir;
  }

  function posText(pos) {
    return pos ? `[${pos[0]},${pos[1]}]` : "-";
  }

  /** 深拷贝地形网格，供逐帧 map destroyed 与爆区预览使用 */
  function cloneGrid(grid) {
    return (grid || []).map((col) => (col ? col.slice() : []));
  }

  /**
   * 十字爆区格子枚举，逻辑与 engine.bombBlastCells 一致。
   * 石墙阻断且不在爆区内；土堆计入爆区并阻断该方向继续延伸。
   */
  function bombBlastCells(grid, position) {
    if (!position || !grid.length) return [];
    const height = grid[0] ? grid[0].length : 0;
    const cells = [position.slice()];
    for (const dir of DIRS) {
      const delta = DIR_DELTA[dir];
      for (let distance = 1; distance <= BOMB_BLAST_RANGE; distance += 1) {
        const x = position[0] + delta[0] * distance;
        const y = position[1] + delta[1] * distance;
        if (x < 0 || y < 0 || x >= grid.length || y >= height) break;
        const terrain = grid[x][y];
        if (terrain === "x") break;
        cells.push([x, y]);
        if (terrain === "m") break;
      }
    }
    return cells;
  }

  function bombFuseLeft(placedFrame, currentFrame) {
    return Math.max(0, BOMB_FUSE_FRAMES - (currentFrame - placedFrame));
  }

  /** 平台 placed 事件带 explodeFrame；本地模拟器 created 需用 placedFrame 推算 */
  function bombFuseLeftForBomb(bomb, currentFrame) {
    if (typeof bomb.explodeFrame === "number") {
      return Math.max(0, bomb.explodeFrame - currentFrame);
    }
    return bombFuseLeft(bomb.placedFrame, currentFrame);
  }

  /**
   * 坦克 objectId → 玩家索引映射。
   * 优先用 meta.players[i].tank（后端 player_tanks 字段，权威：含初始 id/位置/朝向）；
   * 缺失时回退事件扫描（speech / skill cast 的 by 可靠；tank crashed 的 by 是击杀者，不可用）。
   */
  function buildTankMapping(records, playerTanks) {
    const idToBy = new Map();
    const tankIds = [];
    const seen = new Set();
    const firstState = new Map();
    (playerTanks || []).forEach((t, i) => {
      if (t && t.id) {
        idToBy.set(t.id, i);
        seen.add(t.id);
        tankIds.push(t.id);
        if (t.position) firstState.set(t.id, { pos: t.position.slice(), dir: t.direction || "up" });
      }
    });
    const note = (id, st) => {
      if (!id) return;
      if (!seen.has(id)) {
        seen.add(id);
        tankIds.push(id);
      }
      if (st && st.position && !firstState.has(id)) {
        firstState.set(id, { pos: st.position.slice(), dir: st.direction || "up" });
      }
    };
    for (const frame of records) {
      for (const ev of frame || []) {
        if (ev.type === "tank" && ev.objectId) note(ev.objectId, ev.tank);
        if (ev.type === "speech" && ev.objectId) note(ev.objectId, ev.tank);
        if (ev.type === "skill" && ev.sourceObjectId) note(ev.sourceObjectId, null);
        if (ev.type === "bullet" && ev.tank && ev.tank.id) note(ev.tank.id, ev.tank);
        const by = typeof ev.by === "number" ? ev.by : null;
        if (by == null) continue;
        if (ev.type === "speech" && ev.objectId && !idToBy.has(ev.objectId)) idToBy.set(ev.objectId, by);
        if (ev.type === "skill" && ev.action === "cast" && ev.sourceObjectId && !idToBy.has(ev.sourceObjectId)) {
          idToBy.set(ev.sourceObjectId, by);
        }
      }
    }
    // 双人局反推未映射的另一方
    if (tankIds.length === 2) {
      const mapped = tankIds.filter((id) => idToBy.has(id));
      if (mapped.length === 1) {
        const other = tankIds.find((id) => !idToBy.has(id));
        idToBy.set(other, idToBy.get(mapped[0]) === 0 ? 1 : 0);
      }
    }
    return { idToBy, tankIds, firstState };
  }

  /** 逐帧重放 records，输出每帧完整实体快照 */
  function reconstructFrames(data) {
    const records = data.records || [];
    const spawn = (data.map && data.map.spawn) || [];
    const names = data.names || [];
    const { idToBy, tankIds, firstState } = buildTankMapping(records, data.player_tanks);

    const tanks = new Map();
    tankIds.forEach((id, i) => {
      const by = idToBy.has(id) ? idToBy.get(id) : i;
      const sp = spawn[by] || {};
      const fallback = firstState.get(id) || {};
      tanks.set(id, {
        id,
        by,
        pos: (sp.position || fallback.pos || [0, 0]).slice(),
        dir: sp.direction || fallback.dir || "up",
        stars: 0,
        crashed: false,
      });
    });
    const tankByPlayer = new Map();
    for (const t of tanks.values()) tankByPlayer.set(t.by, t.id);
    const pName = (by) => names[by] || `P${by}`;

    let star = null;
    const bullets = new Map();
    // 逐帧地形：同步 map destroyed，保证土堆被毁后爆区预览与渲染一致
    let grid = cloneGrid((data.map && data.map.grid) || []);
    const bombs = new Map();
    let blastFlash = null;
    const frames = [];

    for (let f = 0; f < records.length; f++) {
      const notes = [];
      for (const ev of records[f] || []) {
        if (ev.type === "map" && ev.action === "destroyed" && ev.position) {
          const mx = ev.position[0];
          const my = ev.position[1];
          if (grid[mx] && grid[mx][my] !== undefined) grid[mx][my] = ".";
        }
        // 平台 raw 用 placed + blast；本地模拟器用 created + cells
        if (ev.type === "bomb" && (ev.action === "created" || ev.action === "placed") && ev.objectId && ev.position) {
          bombs.set(ev.objectId, {
            pos: ev.position.slice(),
            by: ev.by,
            placedFrame: typeof ev.placedFrame === "number" ? ev.placedFrame : f,
            explodeFrame: typeof ev.explodeFrame === "number" ? ev.explodeFrame : undefined,
            hidden: !!ev.hidden,
          });
          notes.push(`${pName(ev.by)} 💣放雷${posText(ev.position)}${ev.hidden ? "(草丛)" : ""}`);
        }
        if (ev.type === "bomb" && ev.action === "exploded" && ev.objectId) {
          bombs.delete(ev.objectId);
          // 爆炸高亮直接用事件 cells/blast，避免静态十字与地形截断不一致
          const cells = (ev.cells || ev.blast || []).map((c) => c.slice());
          blastFlash = { cells, untilFrame: f + 1 };
          notes.push(`${pName(ev.by)} 💥爆炸${cells.length}格`);
        }
        if (ev.type === "star" && ev.action === "created") {
          star = ev.position ? ev.position.slice() : null;
          notes.push(`★出现${posText(star)}`);
        }
        if (ev.type === "star" && ev.action === "collected") {
          const t = tanks.get(tankByPlayer.get(ev.by));
          if (t) t.stars += 1;
          star = null;
          notes.push(`${pName(ev.by)} 吃星`);
        }
        if (ev.type === "skill" && ev.action === "cast") {
          notes.push(`${pName(ev.by)} ⚡${ev.skillType || ""}`);
        }
        if (ev.type === "skill" && ev.action === "applied" && ev.to && ev.targetObjectId) {
          const t = tanks.get(ev.targetObjectId);
          if (t) t.pos = ev.to.slice();
          notes.push(`传送到${posText(ev.to)}`);
        }
        if (ev.type === "tank" && ev.action === "turn") {
          const t = tanks.get(ev.objectId);
          if (t) t.dir = rotDir(t.dir, ev.direction);
        }
        if (ev.type === "tank" && ev.action === "go") {
          const t = tanks.get(ev.objectId);
          if (t && ev.position) t.pos = ev.position.slice();
        }
        if (ev.type === "tank" && (ev.action === "crashed" || ev.action === "destroyed")) {
          const t = tanks.get(ev.objectId);
          if (t) t.crashed = true;
          notes.push(`${t ? pName(t.by) : ev.objectId} 💀${ev.action === "crashed" ? "撞毁" : "被摧毁"}`);
        }
        if (ev.type === "bullet" && ev.action === "created") {
          bullets.set(ev.objectId, {
            id: ev.objectId,
            pos: ev.tank && ev.tank.position ? ev.tank.position.slice() : null,
            dir: ev.direction,
            shooterId: ev.tank && ev.tank.id,
          });
          const shooter = tanks.get(ev.tank && ev.tank.id);
          notes.push(`${shooter ? pName(shooter.by) : "?"} 🔫开火(${ev.direction || "?"})`);
        }
        if (ev.type === "bullet" && ev.action === "go") {
          const b = bullets.get(ev.objectId);
          if (b && ev.position) b.pos = ev.position.slice();
        }
        if (ev.type === "bullet" && (ev.action === "hit" || ev.action === "crashed" || ev.action === "destroyed")) {
          // 子弹命中/撞墙后从场上移除
          if (ev.action === "hit") notes.push("💥命中");
          bullets.delete(ev.objectId);
        }
        if (ev.type === "speech") {
          notes.push(`${pName(ev.by)}:「${ev.text}」`);
        }
      }
      frames.push({
        frame: f,
        grid: cloneGrid(grid),
        star: star && star.slice(),
        tanks: [...tanks.values()]
          .sort((a, b) => (a.by ?? 9) - (b.by ?? 9))
          .map((t) => ({
            by: t.by,
            name: pName(t.by),
            pos: t.pos && t.pos.slice(),
            dir: t.dir,
            stars: t.stars,
            crashed: t.crashed,
          })),
        bullets: [...bullets.values()].map((b) => ({
          pos: b.pos && b.pos.slice(),
          dir: b.dir,
        })),
        bombs: [...bombs.values()].map((b) => ({
          pos: b.pos.slice(),
          by: b.by,
          fuseLeft: bombFuseLeftForBomb(b, f),
          hidden: b.hidden,
          previewCells: bombBlastCells(grid, b.pos),
        })),
        blastFlash: blastFlash && f <= blastFlash.untilFrame
          ? blastFlash.cells.map((c) => c.slice())
          : null,
        notes,
      });
    }
    return frames;
  }

  // Canvas 配色（与暗色仪表盘风格一致；canvas 无法用 CSS 变量，故硬编码）
  const TERRAIN_COLORS = {
    x: "#3d4a5d", // 石墙
    m: "#8a6a44", // 土堆
    o: "#2f6b3a", // 草地
    ".": "#141b26", // 空地
  };
  const TANK_COLORS = ["#3b82f6", "#ef4444", "#eab308", "#a855f7"];
  const BOMB_BODY_COLOR = "#78716c";
  const BOMB_PREVIEW_RGBA = "rgba(239,68,68,0.18)";
  const BOMB_FLASH_RGBA = "rgba(249,115,22,0.45)";

  class ReplayViewer {
    /**
     * @param {HTMLElement} root 挂载容器（内容会被清空重建）
     * @param {object} data /api/matches/{id}/replay-raw 响应
     */
    constructor(root, data) {
      this.root = root;
      this.data = data;
      this.frames = reconstructFrames(data);
      this.grid = (data.map && data.map.grid) || [];
      this.width = this.grid.length;
      this.height = this.width ? this.grid[0].length : 0;
      this.idx = 0;
      this.onFrameChange = null;
      this._keyHandler = this._onKey.bind(this);
      this._buildDom();
      document.addEventListener("keydown", this._keyHandler);
      this.seek(0);
    }

    destroy() {
      document.removeEventListener("keydown", this._keyHandler);
      this.root.innerHTML = "";
    }

    _buildDom() {
      const total = this.frames.length;
      const anchor = this.data.anchor_frame;
      this.root.innerHTML = `
        <div class="rv-controls">
          <button type="button" class="rv-btn" data-rv="first" title="首帧 (Home)">⏮</button>
          <button type="button" class="rv-btn" data-rv="prev" title="上一帧 (←)">◀</button>
          <input type="range" class="rv-slider" min="0" max="${Math.max(0, total - 1)}" value="0" />
          <button type="button" class="rv-btn" data-rv="next" title="下一帧 (→)">▶</button>
          <button type="button" class="rv-btn" data-rv="last" title="末帧 (End)">⏭</button>
          <span class="rv-frame-label">f<input type="number" class="rv-frame-input" min="0" max="${Math.max(0, total - 1)}" value="0" /> / ${total - 1}</span>
          ${anchor != null ? `<button type="button" class="rv-btn rv-anchor-btn" data-rv="anchor" title="跳到复盘锚点帧">锚点 f${anchor}</button>` : ""}
        </div>
        <canvas class="rv-canvas"></canvas>
        <div class="rv-status"></div>
        <div class="rv-notes"></div>`;
      this.canvas = this.root.querySelector(".rv-canvas");
      this.slider = this.root.querySelector(".rv-slider");
      this.frameInput = this.root.querySelector(".rv-frame-input");
      this.statusEl = this.root.querySelector(".rv-status");
      this.notesEl = this.root.querySelector(".rv-notes");

      // 画布尺寸：格子取整、上限 30px，保证 15x19 与 19x15 图都放得下
      const cell = Math.max(12, Math.min(30, Math.floor(560 / Math.max(this.width, 1)), Math.floor(440 / Math.max(this.height, 1))));
      this.cell = cell;
      const dpr = window.devicePixelRatio || 1;
      this.canvas.width = this.width * cell * dpr;
      this.canvas.height = this.height * cell * dpr;
      this.canvas.style.width = `${this.width * cell}px`;
      this.canvas.style.height = `${this.height * cell}px`;
      this.ctx = this.canvas.getContext("2d");
      this.ctx.scale(dpr, dpr);

      this.root.querySelectorAll("[data-rv]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const act = btn.dataset.rv;
          if (act === "first") this.seek(0);
          else if (act === "prev") this.seek(this.idx - 1);
          else if (act === "next") this.seek(this.idx + 1);
          else if (act === "last") this.seek(this.frames.length - 1);
          else if (act === "anchor") this.seek(this.data.anchor_frame);
        });
      });
      this.slider.addEventListener("input", () => this.seek(parseInt(this.slider.value, 10)));
      this.frameInput.addEventListener("change", () => this.seek(parseInt(this.frameInput.value, 10)));
    }

    _onKey(e) {
      // 仅在查看器可见且焦点不在输入框时响应
      if (!this.root.isConnected || this.root.closest(".modal")?.hidden) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (e.key === "ArrowLeft") { e.preventDefault(); this.seek(this.idx - 1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); this.seek(this.idx + 1); }
      else if (e.key === "Home") { e.preventDefault(); this.seek(0); }
      else if (e.key === "End") { e.preventDefault(); this.seek(this.frames.length - 1); }
    }

    seek(i) {
      if (!this.frames.length) return;
      const idx = Math.max(0, Math.min(this.frames.length - 1, i | 0));
      this.idx = idx;
      this.slider.value = String(idx);
      this.frameInput.value = String(idx);
      this._render();
      if (typeof this.onFrameChange === "function") this.onFrameChange(idx);
    }

    _render() {
      const fr = this.frames[this.idx];
      const { ctx, cell } = this;
      const frameGrid = fr.grid || this.grid;
      // 地形（逐帧 grid，反映子弹/炸弹摧毁的土堆）
      for (let x = 0; x < this.width; x++) {
        for (let y = 0; y < this.height; y++) {
          ctx.fillStyle = TERRAIN_COLORS[frameGrid[x] && frameGrid[x][y]] || TERRAIN_COLORS["."];
          ctx.fillRect(x * cell, y * cell, cell, cell);
        }
      }
      // 网格线
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.lineWidth = 1;
      for (let x = 0; x <= this.width; x++) {
        ctx.beginPath();
        ctx.moveTo(x * cell + 0.5, 0);
        ctx.lineTo(x * cell + 0.5, this.height * cell);
        ctx.stroke();
      }
      for (let y = 0; y <= this.height; y++) {
        ctx.beginPath();
        ctx.moveTo(0, y * cell + 0.5);
        ctx.lineTo(this.width * cell, y * cell + 0.5);
        ctx.stroke();
      }
      // 待爆炸区预览（淡红十字）
      for (const bomb of fr.bombs || []) {
        if (bomb.previewCells && bomb.previewCells.length) {
          this._drawBombPreview(bomb.previewCells);
        }
      }
      // 爆炸高亮（爆炸帧及后 1 帧）
      if (fr.blastFlash && fr.blastFlash.length) {
        this._drawBlastFlash(fr.blastFlash);
      }
      // 星星
      if (fr.star) this._drawStar(fr.star[0], fr.star[1]);
      // 炸弹本体（复盘上帝视角：hidden 草丛雷也显示，虚线区分）
      for (const bomb of fr.bombs || []) {
        this._drawBomb(bomb);
      }
      // 子弹
      for (const b of fr.bullets) {
        if (b.pos) this._drawBullet(b);
      }
      // 坦克
      for (const t of fr.tanks) {
        if (t.pos) this._drawTank(t);
      }
      // 状态摘要
      const tankText = fr.tanks
        .map((t) => `<span style="color:${TANK_COLORS[t.by % TANK_COLORS.length]}">${t.name}</span> ${posText(t.pos)} ${t.dir} ★${t.stars}${t.crashed ? " 💀" : ""}`)
        .join(" &nbsp;|&nbsp; ");
      const bombCount = (fr.bombs || []).length;
      const urgentFuse = (fr.bombs || []).some((b) => b.fuseLeft <= 3);
      const bombText = `炸弹:${bombCount}${urgentFuse ? " (引信≤3)" : ""}`;
      this.statusEl.innerHTML = `${tankText} &nbsp;|&nbsp; 星:${posText(fr.star)} 子弹:${fr.bullets.length} ${bombText}`;
      this.notesEl.textContent = fr.notes.length ? fr.notes.join("  ·  ") : "";
    }

    _drawBlastOverlay(cells, fillStyle, strokeCenter) {
      const { ctx, cell } = this;
      ctx.fillStyle = fillStyle;
      for (const c of cells) {
        ctx.fillRect(c[0] * cell, c[1] * cell, cell, cell);
      }
      if (strokeCenter && cells.length) {
        const center = cells[0];
        ctx.strokeStyle = "rgba(255,255,255,0.7)";
        ctx.lineWidth = 2;
        ctx.strokeRect(center[0] * cell + 1, center[1] * cell + 1, cell - 2, cell - 2);
      }
    }

    _drawBombPreview(cells) {
      this._drawBlastOverlay(cells, BOMB_PREVIEW_RGBA);
    }

    _drawBlastFlash(cells) {
      this._drawBlastOverlay(cells, BOMB_FLASH_RGBA, true);
    }

    _drawBomb(b) {
      const { ctx, cell } = this;
      const cx = (b.pos[0] + 0.5) * cell;
      const cy = (b.pos[1] + 0.5) * cell;
      const radius = cell * 0.22;
      ctx.globalAlpha = b.hidden ? 0.65 : 1;
      ctx.fillStyle = BOMB_BODY_COLOR;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
      if (b.hidden) {
        ctx.strokeStyle = "rgba(250,250,250,0.55)";
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, radius + 2, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;
      const fuseText = String(b.fuseLeft);
      ctx.fillStyle = b.fuseLeft <= 3 ? "#ef4444" : "#e5e7eb";
      ctx.font = `bold ${Math.max(8, cell * 0.32)}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(fuseText, cx, cy);
    }

    _drawStar(x, y) {
      const { ctx, cell } = this;
      const cx = (x + 0.5) * cell;
      const cy = (y + 0.5) * cell;
      const outer = cell * 0.38;
      const inner = outer * 0.45;
      ctx.fillStyle = "#facc15";
      ctx.beginPath();
      for (let i = 0; i < 10; i++) {
        const r = i % 2 === 0 ? outer : inner;
        const a = -Math.PI / 2 + (i * Math.PI) / 5;
        const px = cx + r * Math.cos(a);
        const py = cy + r * Math.sin(a);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
    }

    _drawBullet(b) {
      const { ctx, cell } = this;
      const cx = (b.pos[0] + 0.5) * cell;
      const cy = (b.pos[1] + 0.5) * cell;
      ctx.fillStyle = "#fb923c";
      ctx.beginPath();
      ctx.arc(cx, cy, cell * 0.16, 0, Math.PI * 2);
      ctx.fill();
      // 弹道尾迹（指向来向）
      const tail = { up: [0, 1], down: [0, -1], left: [1, 0], right: [-1, 0] }[b.dir] || [0, 0];
      ctx.strokeStyle = "rgba(251,146,60,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + tail[0] * cell * 0.4, cy + tail[1] * cell * 0.4);
      ctx.stroke();
    }

    _drawTank(t) {
      const { ctx, cell } = this;
      const x = t.pos[0] * cell;
      const y = t.pos[1] * cell;
      const pad = cell * 0.12;
      const color = t.crashed ? "#6b7280" : TANK_COLORS[t.by % TANK_COLORS.length];
      ctx.fillStyle = color;
      ctx.fillRect(x + pad, y + pad, cell - pad * 2, cell - pad * 2);
      // 朝向楔形
      const cx = x + cell / 2;
      const cy = y + cell / 2;
      const tip = {
        up: [cx, y + pad * 0.5],
        down: [cx, y + cell - pad * 0.5],
        left: [x + pad * 0.5, cy],
        right: [x + cell - pad * 0.5, cy],
      }[t.dir] || [cx, cy];
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(tip[0], tip[1], cell * 0.1, 0, Math.PI * 2);
      ctx.fill();
      if (t.crashed) {
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(x + pad, y + pad);
        ctx.lineTo(x + cell - pad, y + cell - pad);
        ctx.moveTo(x + cell - pad, y + pad);
        ctx.lineTo(x + pad, y + cell - pad);
        ctx.stroke();
      }
      // 星数角标
      if (t.stars > 0) {
        ctx.fillStyle = "#facc15";
        ctx.font = `bold ${Math.max(9, cell * 0.4)}px sans-serif`;
        ctx.textAlign = "right";
        ctx.textBaseline = "bottom";
        ctx.fillText(String(t.stars), x + cell - 1, y + cell - 1);
      }
    }
  }

  window.ReplayViewer = ReplayViewer;
})();
