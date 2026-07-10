// ============================================================
// bt-core.js — 行为树核心引擎（框架骨架）
//
// 6 种基础节点类型。每帧 root.tick(bb) → 至多一个 Action 发出指令。
//
// 节点 tick 返回值：
//   BT_SUCCESS (1) — 条件满足 / 动作已执行
//   BT_FAILURE (0) — 条件不满足 / 无法执行
//   BT_RUNNING (2) — 跨帧继续（保留，暂未使用）
// ============================================================

var BT_SUCCESS = 1;
var BT_FAILURE = 0;
var BT_RUNNING = 2;

/**
 * Selector：依次尝试子节点，第一个非 FAILURE 即为结果。
 * 语义："做第一件能做的事"。同层越靠前优先级越高。
 */
function Selector(name, children) {
  var filtered = [];
  for (var i = 0; i < children.length; i++) {
    if (children[i]) filtered.push(children[i]);
  }
  return {
    type: 'selector', name: name, children: filtered,
    tick: function (bb) {
      for (var i = 0; i < this.children.length; i++) {
        var s = this.children[i].tick(bb);
        if (s !== BT_FAILURE) {
          // BT_DEBUG 时记录路径，供 entry 打 f{frame} path:action 日志
          if (BT_DEBUG) bb._trace.push(this.children[i].name);
          return s;
        }
      }
      return BT_FAILURE;
    }
  };
}

/**
 * Sequence：全部 SUCCESS 才 SUCCESS。典型写法：Guard + Guard + Action。
 */
function Sequence(name, children) {
  var filtered = [];
  for (var i = 0; i < children.length; i++) {
    if (children[i]) filtered.push(children[i]);
  }
  return {
    type: 'sequence', name: name, children: filtered,
    tick: function (bb) {
      for (var i = 0; i < this.children.length; i++) {
        var s = this.children[i].tick(bb);
        if (s !== BT_SUCCESS) return s;
      }
      return BT_SUCCESS;
    }
  };
}

/** Guard：纯条件，无副作用。condFn(bb) → true=SUCCESS。 */
function Guard(name, condFn) {
  return {
    type: 'guard', name: name,
    tick: function (bb) {
      return condFn(bb) ? BT_SUCCESS : BT_FAILURE;
    }
  };
}

/** Action：叶子，执行坦克指令后记 _lastAction。 */
function Action(name, execFn) {
  return {
    type: 'action', name: name,
    tick: function (bb) {
      execFn(bb);
      bb._lastAction = name;
      return BT_SUCCESS;
    }
  };
}

/** When：条件为真才 tick 子树，否则 FAILURE。用于终局提权等。 */
function When(name, condFn, child) {
  return {
    type: 'when', name: name,
    tick: function (bb) {
      return condFn(bb) ? child.tick(bb) : BT_FAILURE;
    }
  };
}

/** Inverter：SUCCESS↔FAILURE。 */
function Inverter(name, child) {
  return {
    type: 'inverter', name: name,
    tick: function (bb) {
      var s = child.tick(bb);
      if (s === BT_SUCCESS) return BT_FAILURE;
      if (s === BT_FAILURE) return BT_SUCCESS;
      return s;
    }
  };
}
