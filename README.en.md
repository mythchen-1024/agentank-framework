# agentank-framework

**Languages / 语言：** [中文](README.md) | **English**

A **behavior-tree tank framework skeleton** for [AgenTank](https://agentank.ai) (open-source reference implementation).

> **What this is**: A buildable, publishable, analyzable architecture scaffold — root priorities, skill slots, commitment mechanism, BT logging, Terser minification, and tooling.  
> **What this is not**: A competition-ready bot. Major nodes / skill slots only have factory signatures and `TODO`/`FAILURE` placeholders — **not guaranteed to fight well**. You fill in the strategy leaves.

## Quick start

```bash
cd agentank-framework
cp .env.example .env          # fill in AGENTANK_KEY_*
npm install                   # optional: install terser for publish minify
node build-new.js --all-skills
python publish-new.py --only demo-teleport --dry-run   # check minify size, no upload
```

Build output: `dist/bt-tank-{teleport|shield|boost|overload|stun|freeze|cloak|poison}.js`

## Dependencies

| Purpose | Requirement |
|---------|-------------|
| Build | Node.js 18+ (22+ recommended) |
| Publish / match / analyze | Python 3 |
| Publish minify | [terser](https://github.com/terser/terser) (`npm i` or global install) |
| Local simulated matches | **Not bundled** — see simulator below |

### Local simulator (external)

For local regression, use the separate repo (no platform quota, can run unpublished code):

**https://github.com/cyhhao/agentank-simulator**

Example:

```bash
# in the agentank-simulator directory
node bin/simulate-local.mjs \
  --bot-a ../agentank-framework/dist/bt-tank-boost.js \
  --bot-b ../agentank-framework/dist/bt-tank-cloak.js \
  --skill-a boost --skill-b cloak \
  --random-map --seed 42 --max-frames 300
```

## Architecture overview

```text
【build-time build-new.js】
  core-utils / bt-* / blackboard / nodes-* / tree-factory / entry
  + skill-params.js
  + skills/{skill}.js          ← stitch exactly 1 skill
        ↓
  dist/bt-tank-{skill}.js  →  publish-new.py (Terser minify by default)

【runtime entry.js per frame】
  refreshBlackboard → buildProfile → buildBehaviorTree? → root.tick(bb)
  → BT_DEBUG / PF logs
```

### Root priority (`ROOT_PRIORITY`)

Smaller numbers tick first. The skeleton keeps only common top layers; put fine-grained strategy into slots yourself:

```text
root Selector
├── cc-check              # freeze skip
├── hard-survival         # hard survival (+ skill escape slot)
├── commit.early[]        # skill early commitment slot
├── soft-survival         # soft survival
├── commit.late[]         # skill late commitment slot
├── skill-attack          # skill attack slot
├── attack / bomb-attack  # generic attack / drop bomb
├── skill-objective       # skill objective slot
├── objective             # regular objective (with pre/mid/post)
└── movement              # movement + skill movement slot
```

To add a root-layer node: add one row to the constants table in [`tree-factory.js`](tree-factory.js), then wire it into `entries`.

### Skill slot interface

Each [`skills/{skill}.js`](skills/) must export:

| Factory | Mount point |
|---------|-------------|
| `skillSurvivalNodes()` | Nested inside hard survival |
| `skillCommitNodes()` | Root early / late |
| `skillAttackNodes(enemySkill)` | Root skill attack |
| `skillObjectiveNodes(profile, enemySkill)` | `.layer` at root; `.pre/mid/post` nested in objective |
| `skillMovementNodes(profile)` | Nested in movement |

Also required: `SKILL_NAME` / `MY_MATCHUP_OVERRIDES` / `SKILL_TUNING`.

[`skill-params.js`](skill-params.js) is **not a node** — it is a matchup parameter dictionary: `DEFAULT_SKILL_PARAMS` (shared baseline) + the current skill's `MY_MATCHUP_OVERRIDES[enemySkill]`, merged by `getSkillMatchupParams(enemySkill)` so skill nodes can read distances/switches without scattering magic numbers in Guards.

### Commitment mechanism

Multi-frame locks go through [`bt-commitment.js`](bt-commitment.js): `bbCommit` registers pure data → root `CommitmentNode` fulfills it. Supports `yieldWhen` (yield + step exclusion zone) and `overrideSoftSurvival` (override soft survival).

### Logging (keep these formats)

| Format | Purpose |
|--------|---------|
| `f{frame} {path}:{action}` | BT_DEBUG path lookup |
| `PF f{frame} skill\|playstyle\|...` | Profile switch timeline (parsed by `match_analyzer`) |

`bbSpeak` has a per-match budget (~32); mark key actions with `important`.

## Directory layout

```text
agentank-framework/
├── bt-core.js / bt-commitment.js   # BT primitives + commitment
├── blackboard.js / entry.js        # blackboard + onIdle
├── tree-factory.js                 # ROOT_PRIORITY + slot assembly
├── nodes-*.js                      # layer factories (strategy TODO)
├── skills/*.js                     # 8 empty skill slots
├── core-utils.js / state-store.js  # minimal utils
├── movement-engine.js              # movement execution (how: safe step / BFS)
├── nodes-movement-v2.js            # movement decisions (when: chase star / patrol tree)
├── build-new.js                    # concat build
├── terser-config.js                # publish minify config
├── publish-new.py                  # publish (minify by default)
├── match_runner.py                 # online batch matches
├── match_analyzer.py               # match ingest to DB (generic)
├── bt_profile_expectations.json    # Gap rules (empty in skeleton)
├── bt_profile_expectations.example.json  # Gap rules fill-in example
├── analysis_db.py / report_server.py
├── analysis/static/                # dashboard (no data.db)
└── tank_profiles.py                # profiles (secrets via .env)
```

## How to fill in strategy

1. In `nodes-survival.js` / `nodes-attack.js` etc., fill leaves with `Sequence(Guard..., Action...)`.
2. In the matching `skills/{skill}.js`, add skill-specific nodes and mount them to slots.
3. Movement: decisions in `nodes-movement-v2.js` (when to chase star / patrol); actual step selection / go in `movement-engine.js` (`moveToward` / `isSafeStep`). Call the engine from Actions — do not hang a behavior tree inside the engine.
4. For multi-frame locks use `bbCommit` + `CommitmentNode`; do not hand-roll memory flags.
5. `node build-new.js --skill xxx` → local simulator regression → `publish-new.py` publish.
6. Bubble text and print output are subject to platform moderation; avoid sensitive content.

Naive starter tips (implement yourself):

- Evaluate bomb threats before chasing stars or firing;
- When same line with no cover, turn then fire;
- With no ray and no star, do a safe patrol — do not idle in place.

## Development iteration mindset

1. **Fix root priority first** — survival > commitment > attack > objective > movement; use a constants table, not half-numbered comments.
2. **Skill slots** — fixed base, differences in `skills/*`; build stitches one skill for dead-code elimination.
3. **Commitments unify multi-frame behavior** — poke grass / dash / last-hit share one fulfillment site; avoid per-feature TTL.
4. **Log contract** — once `f…` / `PF…` are stable, analyzers can reconstruct “why this frame did this”.
5. **Local sim regression** — same seed, old vs new; small samples are noisy — aim for dozens of matches.
6. **Pass the size gate** — platform ~200KiB; audit no string-key access before changing `INTERNAL_PROPS` in `terser-config.js`.
7. **Publish → match → analyze loop** — `publish-new` → `match_runner` → `match_analyzer` → `report_server`.

## Publish / match / analyze

```bash
# publish (Terser minify by default; --no-minify to disable)
python publish-new.py --only demo-teleport --notes "skeleton trial"

# batch ranked matches (uses platform quota; confirm manually before running)
python match_runner.py --only demo-teleport -n 10 --no-analyze

# ingest recent matches + dashboard
python match_analyzer.py --limit 20
python report_server.py --db analysis/data.db
# open the local URL printed by the server
```

Secrets: `AGENTANK_KEY_*` in `.env` — see [`.env.example`](.env.example).

## Code minification

- Single source of truth: [`terser-config.js`](terser-config.js)
- `publish-new.py` calls the Terser API by default (`top_retain: onIdle`, `ecma: 5`, property-mangle whitelist)
- Cap ~**200KiB** (UTF-8); `--dry-run` shows post-minify size without uploading
- `--no-minify` publishes uncompressed source (for debugging; easy to exceed the limit)

For new mangleable internal fields: prefer a `_` prefix, or audit then add to `INTERNAL_PROPS` (no dynamic `obj['field']` access).

## License

MIT — see [LICENSE](LICENSE).
