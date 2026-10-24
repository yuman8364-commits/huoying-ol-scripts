(() => {
  const D = window.HYOL_DEMO;
  const N = window.HYOL_NATIVE;
  const nativeMode = Boolean(N?.enabled);
  if (nativeMode) Object.assign(D.RUN_STATUS, {
    checking: { label: '检查中', title: '正在只读检查窗口', tone: 'info' },
    leaders: { label: '识别中', title: '正在核实队长标签', tone: 'info' },
  });
  const main = document.getElementById('main-content');
  const dock = document.getElementById('run-dock');
  const dialog = document.getElementById('app-dialog');
  const dialogContent = document.getElementById('dialog-content');
  const scenarioSelect = document.getElementById('scenario-select');
  const state = {
    page: 'plan',
    config: D.freshConfig(),
    scenario: 'idle',
    snapshot: null,
    autoTimer: null,
    stopTimer: null,
    userRun: false,
    advancedOpen: false,
    detectedLeaders: '',
    accountFilter: 'all',
    logSearch: '',
    logLevel: 'all',
    logAccount: 'all',
    autoScroll: true,
    detailSelection: null,
    nativePending: false,
    nativeRunId: '',
    logClearedId: 0,
    lastCheck: '',
    lastLeaders: '',
    closeNotice: '',
  };

  const ICONS = {
    plan: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h5M8 17h3"/>',
    progress: '<path d="M5 19V9m7 10V5m7 14v-7"/><path d="M3 21h18"/>',
    logs: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h6M9 16h6"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.4 2.4 0 0 1 4.6 1c0 2-2.3 2.1-2.3 4M12 17h.01"/>',
    shield: '<path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
    play: '<path d="m9 6 9 6-9 6z"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="1"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    alert: '<path d="M12 3 2.8 20h18.4z"/><path d="M12 9v5m0 3h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    users: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/>',
    user: '<circle cx="12" cy="8" r="3"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
    team: '<circle cx="8" cy="9" r="3"/><circle cx="17" cy="8" r="2.5"/><path d="M2 20v-2a6 6 0 0 1 12 0v2m1-6a5 5 0 0 1 7 4v4"/>',
    arena: '<path d="m6 4 6 3 6-3v8c0 4-2.5 7-6 9-3.5-2-6-5-6-9z"/><path d="m9 14 3-6 3 6"/>',
    bond: '<path d="M9 15 6.5 17.5a3.5 3.5 0 0 1-5-5L6 8a3.5 3.5 0 0 1 5 0"/><path d="m15 9 2.5-2.5a3.5 3.5 0 0 1 5 5L18 16a3.5 3.5 0 0 1-5 0M8 12h8"/>',
    weekend: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m4 15 5-5 4 4 3-3 4 4M8 3v3m8-3v3"/>',
    treasure: '<path d="M4 8h16v12H4zM3 4h18v4H3zM9 4v4m6-4v4M10 13h4v3h-4z"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 8a7 7 0 0 1 11.8-1L20 12M4 12l2.1 5a7 7 0 0 0 11.8-1"/>',
    download: '<path d="M12 3v12m-4-4 4 4 4-4M4 20h16"/>',
    folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    file: '<path d="M6 3h9l4 4v14H6zM14 3v5h5"/>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
    arrow: '<path d="M4 12h16m-5-5 5 5-5 5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/>',
    code: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
  };
  function icon(name, extra = '') {
    return `<svg class="icon ${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.info}</svg>`;
  }
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }
  function pad(value) { return String(value).padStart(2, '0'); }
  function nowText() {
    const now = new Date();
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }
  function clockText() { return nowText().slice(11); }
  function statusBadge(status, override = '') {
    const meta = D.STATUS[status] || { label: '待核实', tone: 'warning' };
    return `<span class="status-badge tone-${meta.tone}">${esc(override || meta.label)}</span>`;
  }
  function runMeta(status) { return D.RUN_STATUS[status] || D.RUN_STATUS.idle; }
  function clearTimers() {
    clearInterval(state.autoTimer);
    clearTimeout(state.stopTimer);
    state.autoTimer = null;
    state.stopTimer = null;
  }
  function toast(message, tone = 'info') {
    const region = document.getElementById('toast-region');
    const item = document.createElement('div');
    item.className = `toast ${tone}`;
    item.innerHTML = `${icon(tone === 'warning' ? 'alert' : 'check')}<span>${esc(message)}</span>`;
    region.appendChild(item);
    while (region.children.length > 3) region.firstElementChild.remove();
    setTimeout(() => item.remove(), 3600);
  }

  function metrics(snapshot = state.snapshot) {
    const result = { done: 0, running: 0, pending: 0, offline: 0, new: 0, skipped: 0 };
    if (!snapshot) return result;
    snapshot.accounts.forEach((account) => snapshot.taskCodes.forEach((code) => {
      const status = account.tasks[code]?.status || 'new';
      result[status] = (result[status] || 0) + 1;
    }));
    result.attention = result.pending + result.offline + result.skipped;
    return result;
  }

  function render() {
    const active = document.activeElement;
    const focus = nativeMode && active?.id === 'log-search'
      ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
    const oldScroll = main.scrollTop;
    const oldTableScroll = main.querySelector('.table-wrap')?.scrollLeft || 0;
    renderHeader();
    renderNav();
    renderMain();
    renderDock();
    applyNativeLabels();
    scenarioSelect.value = state.scenario;
    if (nativeMode) {
      if (state.page === 'plan' && (N.info?.busy || state.nativePending)) {
        main.querySelectorAll('input,select,button,summary').forEach((e) => {
          if (e.tagName !== 'SUMMARY') e.disabled = true;
        });
      }
      main.scrollTop = oldScroll;
      const table = main.querySelector('.table-wrap');
      if (table) table.scrollLeft = oldTableScroll;
      if (focus) {
        const next = document.getElementById(focus.id);
        next?.focus({ preventScroll: true });
        next?.setSelectionRange(focus.start, focus.end);
      }
    }
  }

  function renderHeader() {
    const content = {
      plan: ['工作台', '任务计划', '组织本轮账号与任务，确认执行清单后开始。'],
      progress: ['仅当前运行', '本轮进度', '查看已核实结果、待处理原因与证据；不显示历史累计。'],
      logs: ['运行诊断', '运行日志', '按账号、级别或事件检索；长日志完整换行保留。'],
    }[state.page];
    document.getElementById('page-eyebrow').textContent = content[0];
    document.getElementById('page-title').textContent = content[1];
    document.getElementById('page-description').textContent = content[2];
    const busy = nativeMode ? (!N.ready || N.info?.busy || state.nativePending) : ['running', 'stopping'].includes(state.snapshot?.status);
    document.querySelector('[data-action="check-window"]').disabled = busy;
  }

  function renderNav() {
    document.querySelectorAll('.nav-item[data-page]').forEach((button) => {
      const active = button.dataset.page === state.page;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-icon]').forEach((element) => {
      if (!element.children.length) element.innerHTML = icon(element.dataset.icon);
    });
    const count = metrics().attention;
    const badge = document.getElementById('nav-attention');
    badge.hidden = !count;
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.title = `${count} 个任务状态需要关注`;
  }

  function renderMain() {
    if (state.page === 'plan') main.innerHTML = renderPlan();
    if (state.page === 'progress') main.innerHTML = renderProgress();
    if (state.page === 'logs') main.innerHTML = renderLogs();
  }

  function renderDock() {
    const snapshot = state.snapshot;
    const status = nativeMode && N.info?.busy && N.info.operation !== 'run'
      ? (N.info.operation === 'leaders' ? 'leaders' : 'checking') : snapshot?.status || 'idle';
    const meta = runMeta(status);
    const plan = D.planFor(state.config);
    let title = meta.title;
    let subtitle = !snapshot || state.scenario === 'idle'
      ? `${plan.title} · ${plan.taskCodes.length} 个执行环节`
      : `${snapshot.runId}${nativeMode ? '' : ' · 界面演示数据'}`;
    if (status === 'running') {
      const current = findCurrent('running') || findCurrent('pending') || findCurrent('offline');
      if (current) subtitle = `${current.account.label} · ${D.taskLabel(current.code)} · ${current.account.identity}`;
      if (snapshot.warning) title = '执行中 · 进度显示待刷新';
    }
    if (status === 'stopping') subtitle = '停止交接中，保留断点；不会开始新的任务动作。';
    if (status === 'stopped') subtitle = '未核实内容保留待处理；再次开始将创建新运行。';
    if (status === 'pending') subtitle = '请查看待核实账号与原因；未确认项目不计完成。';
    if (status === 'not_open') subtitle = '活动未开放，未执行且不计完成。';
    const active = nativeMode ? Boolean(N.info?.busy || state.nativePending) : ['running', 'stopping'].includes(status);
    let controls = active
      ? `${state.page !== 'progress' ? '<button class="button button-secondary" data-page="progress">查看进度</button>' : ''}<span class="key-hint"><kbd>F12</kbd> 紧急停止</span><button class="button button-danger button-large" data-action="stop" ${status === 'stopping' ? 'disabled' : ''}>${icon('stop')}${status === 'stopping' ? '正在停止…' : '停止本轮'}</button>`
      : `${state.page !== 'plan' ? '<button class="button button-secondary" data-page="plan">调整任务</button>' : ''}<button class="button button-primary button-large" data-action="start">${icon('play')}${['stopped', 'pending', 'complete', 'not_open'].includes(status) ? '开始新运行' : '开始本轮'}</button>`;
    if (nativeMode && !N.ready) controls = '<button class="button button-primary button-large" disabled>连接运行层中…</button>';
    dock.innerHTML = `<div class="dock-status"><span class="dock-status-icon tone-${meta.tone}">${icon(active ? 'clock' : status === 'complete' ? 'check' : status === 'idle' ? 'plan' : 'alert')}</span><div class="dock-copy"><strong>${esc(title)}</strong><span>${esc(subtitle)}</span></div></div><div class="dock-actions">${controls}</div>`;
  }

  function renderPlan() {
    const section = state.config.section;
    return `<div class="plan-layout"><div class="plan-editor">
      <div class="mode-switch" role="tablist" aria-label="任务组织方式">
        ${modeCard('daily', 'user', '单人 / 多账号', '当前窗口或最多六个标签')}
        ${modeCard('team', 'team', '组队任务', '自动建队或衔接已有队伍')}
        ${modeCard('independent', 'arena', '独立任务', '竞技场、羁绊、周末与挖宝')}
      </div>
      ${section === 'daily' ? renderDaily() : section === 'team' ? renderTeam() : renderIndependent()}
    </div>${renderSummary()}</div>`;
  }

  function modeCard(code, iconName, title, description) {
    const active = state.config.section === code;
    return `<button class="mode-card ${active ? 'is-active' : ''}" data-section="${code}" role="tab" aria-selected="${active}"><span class="mode-icon">${icon(iconName)}</span><span class="mode-copy"><strong>${title}</strong><span>${description}</span></span></button>`;
  }

  function cardHeader(number, title, description, tools = '') {
    return `<div class="card-header"><div><h2>${number ? `<span class="section-number">${number}</span>` : ''}${title}</h2><p>${description}</p></div>${tools ? `<div class="card-tools">${tools}</div>` : ''}</div>`;
  }

  function renderDaily() {
    const config = state.config;
    const tools = `<button class="text-button" data-action="clear-tasks" data-task-group="solo">清空</button><button class="text-button" data-action="default-tasks" data-task-group="solo">恢复默认</button>`;
    return `<section class="card">${cardHeader('01', '账号范围', '标签仅代表窗口位置；账号身份在正式运行时重新核实。')}
      <div class="card-body"><div class="choice-grid">
        ${scopeChoice('current', '当前账号', '只操作当前游戏窗口', 'user')}
        ${scopeChoice('multi', '多个账号', '按当前配置顺序处理 1～6 个标签', 'users')}
      </div>${config.scope === 'multi' ? renderAccountCount() : '<div class="inline-note">' + icon('info') + '<span>单窗口任务未确认身份时显示“待核验”，不会显示为在线。</span></div>'}</div></section>
      <section class="card">${cardHeader('02', '选择单人任务', '执行顺序沿用现有 SOLO_OPTIONS，而不是点击顺序。', tools)}<div class="card-body">
        ${renderTaskGrid(D.SOLO_TASKS, config.tasks, 'solo')}
        ${renderSoloParameters()}
      </div></section>`;
  }

  function scopeChoice(value, title, description, iconName) {
    const config = state.config;
    return `<div class="choice-card"><input id="scope-${value}" type="radio" name="scope" value="${value}" data-config="scope" ${config.scope === value ? 'checked' : ''}><label for="scope-${value}"><strong>${iconName === 'user' ? '当前窗口' : '多标签'} · ${title}</strong><small>${description}</small></label></div>`;
  }

  function renderAccountCount() {
    const count = Number(state.config.accountCount);
    return `<div class="account-count-row"><div><strong>本轮最多处理账号数</strong><small>从标签 1 开始，按现有配置顺序执行</small></div><div class="stepper"><button data-action="account-minus" aria-label="减少账号" ${count <= 1 ? 'disabled' : ''}>−</button><output aria-label="账号数量">${count} / 6</output><button data-action="account-plus" aria-label="增加账号" ${count >= 6 ? 'disabled' : ''}>+</button></div></div>
      <div class="account-slots">${Array.from({ length: 6 }, (_, index) => `<span class="account-slot ${index < count ? 'is-included' : ''}">标签 ${index + 1}${index < count ? ' · 纳入本轮' : ''}</span>`).join('')}</div>`;
  }

  function taskTools(group) {
    return `<button class="text-button" data-action="clear-tasks" data-task-group="${group}">清空</button><button class="text-button" data-action="default-tasks" data-task-group="${group}">恢复默认</button>`;
  }

  function renderTaskGrid(tasks, selected, group) {
    return `<div class="task-grid">${tasks.map((task) => {
      const checked = selected.includes(task.code);
      return `<label class="task-card ${checked ? 'is-selected' : ''}"><input type="checkbox" data-task-group="${group}" data-task-code="${task.code}" ${checked ? 'checked' : ''}><span class="task-card-content"><span class="task-card-title">${task.label}${!task.default ? '<span class="mini-tag">默认不选</span>' : ''}</span><span class="task-card-description">${task.description}</span></span></label>`;
    }).join('')}</div>`;
  }

  function renderSoloParameters() {
    const config = state.config;
    const codes = D.selectedSoloTasks(config);
    const arena = codes.includes('arena') ? `<div class="parameter-box"><h3>竞技场结束条件</h3><p>默认以已核实战令归零结束；指定胜场只是辅助目标。</p>${arenaControls()}</div>` : '';
    const fixed = [
      codes.includes('daruma') ? '达摩：连续确认奖励次数为 0 并回村后才记完成。' : '',
      codes.includes('weekend') ? '周末任务：拼图三个难度与达摩；未开放时跳过且不计完成。' : '',
      codes.includes('treasure') ? '挖宝：每号固定 5 分钟，只使用已有地图，到时暂停并回村。' : '',
    ].filter(Boolean);
    return `<details class="advanced-panel" data-advanced ${state.advancedOpen ? 'open' : ''}><summary><span>任务参数与收尾 <span class="advanced-summary">· ${config.arenaTargetMode === 'tickets' ? '战令归零' : `${config.arenaWins} 胜场`} · ${config.finalManaged ? '最后托管开启' : '最后托管关闭'}</span></span></summary><div class="advanced-content">
      ${arena}${fixed.length ? `<div class="fixed-rule">${icon('info')}<div>${fixed.map((line) => `<div>${line}</div>`).join('')}</div></div>` : ''}
      ${commonControls()}
    </div></details>`;
  }

  function arenaControls() {
    const config = state.config;
    return `<div class="form-row"><div class="radio-group">
      ${radioOption('arenaTargetMode', 'tickets', '战令归零（默认）')}
      ${radioOption('arenaTargetMode', 'wins', '指定胜场（辅助）')}
    </div><label class="field field-inline"><span>目标胜场</span><input type="number" min="1" max="10" value="${config.arenaWins}" data-config="arenaWins" ${config.arenaTargetMode !== 'wins' ? 'disabled' : ''}></label></div>`;
  }

  function radioOption(key, value, label) {
    return `<label class="radio-option"><input type="radio" name="${key}" value="${value}" data-config="${key}" ${state.config[key] === value ? 'checked' : ''}><span>${label}</span></label>`;
  }

  function commonControls() {
    const config = state.config;
    return `<div class="parameter-box"><h3>阵容与最后收尾</h3><p>完成判定仍须包含任务、领奖与回村证据；托管不替代这些判定。</p><div class="form-row"><label class="field"><span>阵容</span><select data-config="formation"><option value="keep" ${config.formation === 'keep' ? 'selected' : ''}>保持不变（默认）</option><option value="main" ${config.formation === 'main' ? 'selected' : ''}>主力阵容</option><option value="fast" ${config.formation === 'fast' ? 'selected' : ''}>速刷阵容</option></select></label><label class="switch-label"><input type="checkbox" data-config="finalManaged" ${config.finalManaged ? 'checked' : ''}>最后进入托管（默认开启）</label></div></div>`;
  }

  function renderTeam() {
    const config = state.config;
    return `<section class="card">${cardHeader('01', '组队方式', '两种流程分别映射 team 与 team_then_multi，不混用参数。')}
      <div class="card-body"><div class="team-mode-tabs"><button class="team-mode-tab ${config.teamMode === 'auto' ? 'is-active' : ''}" data-team-mode="auto">自动建队</button><button class="team-mode-tab ${config.teamMode === 'pipeline' ? 'is-active' : ''}" data-team-mode="pipeline">已有队伍 → 多人单人</button></div>
      ${config.teamMode === 'auto' ? renderLegacyTeam() : renderPipelineTeam()}</div></section>`;
  }

  function renderLegacyTeam() {
    const config = state.config;
    return `<div class="phase-flow"><div class="phase-card"><strong>先组建并核实队伍</strong><span>只关联已识别的队长与成员，不邀请未知角色</span></div><div class="phase-arrow">${icon('arrow')}</div><div class="phase-card"><strong>再完成活动与收尾</strong><span>核实奖励、解散、回村及所选托管</span></div></div>
      <div class="form-row"><label class="field"><span>队长标签</span><select data-config="leader">${[1, 2, 3, 4, 5, 6].map((slot) => `<option value="${slot}" ${config.leader === slot ? 'selected' : ''}>标签 ${slot}</option>`).join('')}</select></label><label class="switch-label"><input type="checkbox" data-config="friendShortcut" ${config.friendShortcut ? 'checked' : ''}>按好友列表邀请两名队员</label></div>
      <div class="form-row" style="margin-top:15px"><div class="field"><span>队员标签（1 或 2 名，不能与队长重复）</span><div class="member-picker">${[1, 2, 3, 4, 5, 6].map((slot) => `<button class="member-chip" data-action="toggle-member" data-slot="${slot}" aria-pressed="${config.members.includes(slot)}" ${config.friendShortcut || config.leader === slot ? 'disabled' : ''}>标签 ${slot}</button>`).join('')}</div></div></div>
      ${config.friendShortcut ? `<div class="inline-note">${icon('info')}<span>好友快捷方式由画面确定两名队员，手动队员选择暂不生效；RunRequest 仍保留原控件值。</span></div>` : ''}
      <div class="divider"></div><div class="card-header" style="padding:0"><div><h2>组队活动</h2><p>默认选择副本、通缉与强者；其他活动按需展开选择。</p></div><div class="card-tools">${taskTools('team')}</div></div>
      <div style="margin-top:14px">${renderTaskGrid(D.TEAM_TASKS, config.teamTasks, 'team')}</div>
      <details class="advanced-panel" data-advanced ${state.advancedOpen ? 'open' : ''}><summary><span>阵容与收尾 <span class="advanced-summary">· 高级设置</span></span></summary><div class="advanced-content">${commonControls()}</div></details>`;
  }

  function renderPipelineTeam() {
    const config = state.config;
    return `<div class="phase-flow"><div class="phase-card"><strong>阶段 1 · 已核实队伍</strong><span>两队先开战，再依次等待结算；结果未知不开始下一轮</span></div><div class="phase-arrow">${icon('arrow')}</div><div class="phase-card"><strong>阶段 2 · 解散后单人</strong><span>核实成员、无队与木叶主界面，再执行所选日常</span></div></div>
      <div class="parameter-box"><div class="form-row"><label class="switch-label"><input type="checkbox" data-config="pipelineAuto" ${config.pipelineAuto ? 'checked' : ''}>自动发现当前队伍</label><button class="button button-secondary button-small" data-action="detect-leaders">${icon('eye')}查看当前队长</button></div>
      <div class="form-row" style="margin-top:13px">${[0, 1].map((index) => `<label class="field"><span>第 ${index + 1} 队队长${index === 1 ? '（可不选）' : ''}</span><select data-pipeline-leader="${index}" ${config.pipelineAuto ? 'disabled' : ''}>${index === 1 ? `<option value="" ${!config.pipelineLeaders[index] ? 'selected' : ''}>不选</option>` : ''}${[1, 2, 3, 4, 5, 6].map((slot) => `<option value="${slot}" ${config.pipelineLeaders[index] === slot ? 'selected' : ''}>标签 ${slot}</option>`).join('')}</select></label>`).join('')}</div>
      <div class="inline-note">${icon('info')}<span>${state.detectedLeaders || '只读识别最多六个标签；只操作已核实队长，识别按钮不执行游戏任务。'}</span></div></div>
      <div class="divider"></div><div class="card-header" style="padding:0"><div><h2>多人阶段任务</h2><p>副本须核实日常与友情奖励归零；强者不会购买体力。</p></div><div class="card-tools">${taskTools('pipeline')}</div></div>
      <div style="margin:14px 0 20px">${renderTaskGrid(D.PIPELINE_TASKS, config.pipelineTasks, 'pipeline')}</div>
      <div class="card-header" style="padding:0"><div><h2>解散后执行的单人任务</h2><p>与“单人 / 多账号”共用当前选择，固定最多六个账号。</p></div><div class="card-tools">${taskTools('solo')}</div></div>
      <div style="margin-top:14px">${renderTaskGrid(D.SOLO_TASKS, config.tasks, 'solo')}</div>
      ${renderSoloParameters()}`;
  }

  function renderIndependent() {
    const config = state.config;
    return `<section class="card">${cardHeader('01', '选择独立任务', '每次只运行一种独立任务，使用当前游戏窗口。')}
      <div class="card-body"><div class="independent-grid">${D.INDEPENDENT_TASKS.map((task) => `<button class="independent-card ${config.independent === task.code ? 'is-active' : ''}" data-independent="${task.code}" aria-pressed="${config.independent === task.code}">${icon(task.icon)}<strong>${task.label}</strong><small>${task.description}</small></button>`).join('')}</div></div></section>
      <section class="card">${cardHeader('02', `${D.taskLabel(config.independent)}设置`, '只显示当前任务实际支持的参数；固定规则不可在原型中改写。')}<div class="card-body">${renderIndependentSettings()}</div></section>`;
  }

  function renderIndependentSettings() {
    const config = state.config;
    if (config.independent === 'arena') {
      return `<div class="parameter-box"><h3>结束条件与阵容</h3><p>战令次数和胜场均以核实记录为准，失败不计入胜场。</p>${arenaControls()}</div><div style="margin-top:10px">${commonControls()}</div>`;
    }
    if (config.independent === 'bond') {
      return `<div class="parameter-box"><h3>羁绊轮次</h3><p>原战斗或收尾未关联同一实名账号时，不重复匹配、回村或托管。</p><div class="form-row"><label class="field"><span>目标场数（1～99）</span><input type="number" min="1" max="99" value="${config.bondCount}" data-config="bondCount"></label><label class="switch-label"><input type="checkbox" data-config="newBondRound" ${config.newBondRound ? 'checked' : ''}>明确开始新一轮羁绊</label></div></div>
      <div class="fixed-rule" style="margin-top:10px">${icon('shield')}<div>默认优先核实并收尾原轮次；“开始新一轮”会在演示开始后自动复位。阵容控件不适用；回村与最后托管沿用当前设置（最后托管：${config.finalManaged ? '开启' : '关闭'}），此处不提供额外开关。</div></div>`;
    }
    const message = config.independent === 'weekend'
      ? '拼图三个难度与达摩依次核实；活动未开放时不执行，也不计完成。'
      : '只使用已有地图，每账号独立计时 5 分钟；加载时只等待，到时核实暂停与回村。';
    return `<div class="fixed-rule">${icon(config.independent === 'weekend' ? 'weekend' : 'treasure')}<div>${message}<br>实际购买始终禁用，识别阈值与断点规则不在新界面中修改。</div></div><div style="margin-top:10px">${commonControls()}</div>`;
  }

  function renderSummary() {
    const config = state.config;
    const plan = D.planFor(config);
    const error = D.validate(config);
    const status = state.snapshot?.status || 'idle';
    const formation = { keep: '保持不变', main: '主力阵容', fast: '速刷阵容' }[config.formation];
    let index = 0;
    const phases = plan.phases.map((phase) => `<div class="summary-phase"><div class="summary-phase-label">${esc(phase.label)}</div>${phase.tasks.length ? phase.tasks.map((code) => {
      index += 1;
      return `<div class="summary-task"><span class="summary-index">${index}</span><div><strong>${D.taskLabel(code)}</strong><small>${esc(D.detailFor(code, config))}</small></div></div>`;
    }).join('') : '<div class="summary-empty">尚未选择任务</div>'}</div>`).join('');
    return `<aside class="card summary-card"><div class="summary-head"><h2>执行清单</h2>${statusBadge(status === 'not_open' ? 'skipped' : status === 'complete' ? 'done' : status === 'running' ? 'running' : status === 'pending' ? 'pending' : 'new', runMeta(status).label)}</div><div class="summary-content"><div class="summary-scope"><strong>${esc(plan.title)}</strong><span>开始前只创建演示请求；正式版将在运行时再核实账号与画面。</span></div><div class="summary-phases">${phases}</div><div class="summary-settings"><div class="summary-setting"><span>阵容</span><strong>${config.section === 'independent' && config.independent === 'bond' ? '不适用' : formation}</strong></div><div class="summary-setting"><span>最后托管</span><strong>${config.finalManaged ? '开启（默认）' : '关闭'}</strong></div><div class="summary-setting"><span>购买</span><strong>始终禁用</strong></div></div><div class="safety-note">${icon('shield')}<span>${error ? `配置待修正：${esc(error)}` : '完成必须有任务、领奖与回村证据。F12、识别阈值、INI 与断点格式仍由原运行层负责。'}</span></div><button class="button button-secondary button-small" style="width:100%;margin-top:13px" data-action="show-request">${icon('code')}查看演示 RunRequest</button></div></aside>`;
  }

  function findCurrent(status) {
    if (!state.snapshot) return null;
    for (const account of state.snapshot.accounts) {
      for (const code of state.snapshot.taskCodes) {
        if (account.tasks[code]?.status === status) return { account, code };
      }
    }
    return null;
  }

  function accountOffline(account) {
    return /掉线|离线|断开|中断|offline/i.test(account.health || '')
      || state.snapshot.taskCodes.some(code => account.tasks[code]?.status === 'offline');
  }

  function accountCategory(account) {
    const records = state.snapshot.taskCodes.map((code) => account.tasks[code]);
    if (accountOffline(account)) return 'offline';
    if (records.some((rec) => rec.status === 'pending' || rec.status === 'skipped')) return 'attention';
    if (records.some((rec) => rec.status === 'running')) return 'running';
    if (records.every((rec) => rec.status === 'done')) return 'done';
    return 'waiting';
  }

  function renderProgress() {
    const snapshot = state.snapshot;
    if (!snapshot) {
      return `<section class="card empty-state"><span class="empty-state-icon">${icon('progress')}</span><h2>尚未开始本轮</h2><p>先在任务计划中确认账号范围与执行清单。开始后，这里只显示新运行的核实记录，不带入上一轮进度。</p><button class="button button-primary" data-page="plan">${icon('plan')}前往任务计划</button></section>`;
    }
    const meta = runMeta(snapshot.status);
    const counts = metrics();
    const current = findCurrent('running') || findCurrent('pending') || findCurrent('offline');
    const subtitle = current ? `当前关注：${current.account.label} · ${D.taskLabel(current.code)}` : snapshot.status === 'complete' ? '任务、回村与最后托管均已核实' : '没有正在执行的任务';
    return `<section class="card run-hero"><div class="run-heading"><span class="run-hero-icon tone-${meta.tone}">${icon(snapshot.status === 'complete' ? 'check' : snapshot.status === 'running' ? 'clock' : 'alert')}</span><div><h2>${meta.title}</h2><p>${esc(subtitle)}<br><span class="mono">${esc(snapshot.runId)}</span> · 开始于 ${esc(snapshot.startedAt)}</p></div></div><div class="run-metrics"><div class="run-metric success"><strong>${counts.done}</strong><span>已核实完成</span></div><div class="run-metric"><strong>${counts.running}</strong><span>执行中</span></div><div class="run-metric ${counts.offline ? 'danger' : 'warning'}"><strong>${counts.attention}</strong><span>待处理 / 跳过</span></div><div class="run-metric"><strong>${counts.new}</strong><span>尚未执行</span></div></div></section>
      ${renderProgressNotice()}
      <section class="card progress-card"><div class="progress-toolbar"><div><h2>账号与任务明细</h2><p>点击任务单元格查看次数、未决动作、回村与演示证据。</p></div><div class="toolbar-actions"><select id="account-filter" aria-label="筛选账号"><option value="all" ${state.accountFilter === 'all' ? 'selected' : ''}>全部账号</option><option value="attention" ${state.accountFilter === 'attention' ? 'selected' : ''}>需要关注</option><option value="running" ${state.accountFilter === 'running' ? 'selected' : ''}>执行中</option><option value="done" ${state.accountFilter === 'done' ? 'selected' : ''}>已完成</option><option value="offline" ${state.accountFilter === 'offline' ? 'selected' : ''}>掉线</option></select><button class="button button-secondary button-small" data-action="refresh-progress">${icon('refresh')}刷新</button></div></div>
      <div class="table-wrap"><table class="progress-table"><thead><tr><th>账号 / 标签</th><th>账号状态 / 当前任务</th>${snapshot.taskCodes.map((code) => `<th>${D.taskLabel(code)}</th>`).join('')}</tr></thead><tbody>${renderAccountRows()}</tbody></table></div><div class="progress-footer"><span>最近核实：${esc(snapshot.updatedAt || '暂无记录')}${snapshot.warning ? ' · 当前为上次有效快照' : ''}</span><span class="current-only">${icon('shield')}只读浏览 · 仅本轮 · 查看不点击游戏</span></div></section>`;
  }

  function renderProgressNotice() {
    const snapshot = state.snapshot;
    if (snapshot.warning) {
      return `<div class="attention-banner danger">${icon('alert')}<div><strong>进度刷新失败，已保留上次核实快照</strong><p>${esc(snapshot.warning)} 空数据或读取故障不会被解释为任务完成，任务入口不受影响。</p></div><button class="button button-secondary button-small" data-action="refresh-progress">重试刷新</button></div>`;
    }
    if (snapshot.status === 'not_open') {
      return `<div class="attention-banner">${icon('weekend')}<div><strong>活动未开放 · 未执行且不计完成</strong><p>执行日 ${esc(snapshot.day)}：所选周末活动当前不在开放时间。保留请求与原因，不用“跳过”冒充完成。</p></div></div>`;
    }
    if (snapshot.status === 'pending') {
      const current = findCurrent('pending');
      return `<div class="attention-banner">${icon('alert')}<div><strong>${current ? `${current.account.label} · ${D.taskLabel(current.code)}待核实` : '本轮有待处理项'}</strong><p>${esc(current?.account.tasks[current.code].last_error || '任务、领奖或回村证据尚未齐全。')}</p></div>${current ? `<button class="button button-secondary button-small" data-action="open-detail" data-account="${current.account.slot}" data-task="${current.code}">查看原因</button>` : ''}</div>`;
    }
    const offline = state.snapshot.accounts.find(accountOffline);
    if (offline) {
      return `<div class="attention-banner danger">${icon('alert')}<div><strong>${offline.label} 掉线，本轮已跳过</strong><p>该账号未记为完成；其他已核实账号继续。重新核实窗口前不会盲目执行内容操作。</p></div></div>`;
    }
    if (snapshot.status === 'stopping') {
      return `<div class="attention-banner">${icon('stop')}<div><strong>停止中 · 正在结束本轮</strong><p>已发出停止请求并保留断点；等待当前交接结束，不开始新的任务动作。</p></div></div>`;
    }
    if (snapshot.status === 'stopped') {
      return `<div class="attention-banner info">${icon('info')}<div><strong>本轮已停止，未核实内容保留</strong><p>原执行中任务已转为待核实；再次开始会根据当前选择与画面创建新运行，不提供旧批次切换。</p></div></div>`;
    }
    return '';
  }

  function renderAccountRows() {
    const snapshot = state.snapshot;
    const accounts = snapshot.accounts.filter((account) => {
      const category = accountCategory(account);
      if (state.accountFilter === 'all') return true;
      if (state.accountFilter === 'attention') return ['attention', 'offline'].includes(category);
      return category === state.accountFilter;
    });
    if (!accounts.length) return `<tr><td colspan="${snapshot.taskCodes.length + 2}"><div class="log-empty">没有符合当前筛选的账号。<button class="text-button" data-action="clear-account-filter">清除筛选</button></div></td></tr>`;
    return accounts.map((account) => {
      const current = snapshot.status === 'not_open' ? '—' : D.currentTask(account, snapshot.taskCodes);
      const offline = accountOffline(account);
      return `<tr><td><div class="account-name"><span class="account-avatar">${account.slot}</span><div><strong>${account.label}</strong><small title="${esc(account.identity)}">${esc(account.identity)}</small></div></div></td><td><span class="health-text ${offline ? 'offline' : ''}">${offline ? '掉线' : account.health}</span><small class="muted" style="display:block;margin-top:4px;font-size:9px">${esc(current)}</small></td>${snapshot.taskCodes.map((code) => {
        const rec = account.tasks[code];
        const label = snapshot.status === 'not_open' && rec.status === 'skipped' ? '活动未开放' : '';
        const hint = rec.status === 'done' ? '查看核实记录' : rec.status === 'pending' ? '查看待处理原因' : rec.status === 'offline' ? '本轮已跳过' : rec.status === 'running' ? '结果核实中' : rec.status === 'skipped' ? '未执行' : '等待执行';
        return `<td><button class="task-cell" data-action="open-detail" data-account="${account.slot}" data-task="${code}" aria-label="${esc(`${account.label} ${D.taskLabel(code)} ${D.STATUS[rec.status]?.label || rec.status}`)}">${statusBadge(rec.status, label)}<small>${hint}</small></button></td>`;
      }).join('')}</tr>`;
    }).join('');
  }

  function renderLogs() {
    const logs = state.snapshot?.logs || [];
    const accounts = [...new Set(logs.map((entry) => entry.account))];
    return `<section class="card logs-card"><div class="logs-toolbar"><input id="log-search" type="search" placeholder="搜索事件、账号或日志内容" value="${esc(state.logSearch)}" aria-label="搜索日志"><select id="log-level" aria-label="日志级别"><option value="all" ${state.logLevel === 'all' ? 'selected' : ''}>全部级别</option><option value="info" ${state.logLevel === 'info' ? 'selected' : ''}>信息</option><option value="success" ${state.logLevel === 'success' ? 'selected' : ''}>已确认</option><option value="warning" ${state.logLevel === 'warning' ? 'selected' : ''}>注意</option><option value="error" ${state.logLevel === 'error' ? 'selected' : ''}>异常</option></select><select id="log-account" aria-label="日志账号"><option value="all">全部账号</option>${accounts.map((account) => `<option value="${esc(account)}" ${state.logAccount === account ? 'selected' : ''}>${esc(account)}</option>`).join('')}</select><span class="toolbar-spacer"></span><button class="button button-secondary button-small" data-action="export-logs">${icon('download')}导出演示日志</button><button class="button button-ghost button-small" data-action="clear-logs">${icon('trash')}清空显示</button></div><div class="logs-subbar"><span id="log-count">${filteredLogs().length} / ${logs.length} 条 · 仅界面演示数据</span><div><label class="switch-label"><input id="auto-scroll" type="checkbox" ${state.autoScroll ? 'checked' : ''}>新日志自动到底部</label><button class="text-button" data-action="open-log-directory">${icon('folder')}打开日志目录</button></div></div><div class="table-wrap"><div class="log-stream">${renderLogRows()}</div></div></section>`;
  }

  function filteredLogs() {
    const query = state.logSearch.trim().toLowerCase();
    return (state.snapshot?.logs || []).filter((entry) => {
      if (state.logLevel !== 'all' && entry.level !== state.logLevel) return false;
      if (state.logAccount !== 'all' && entry.account !== state.logAccount) return false;
      if (query && !`${entry.account} ${entry.event} ${entry.message}`.toLowerCase().includes(query)) return false;
      return true;
    });
  }

  function renderLogRows() {
    const logs = filteredLogs();
    if (!logs.length) return `<div class="log-empty">${state.snapshot ? '没有符合当前条件的日志。' : '尚未开始本轮，暂无演示日志。'}<br><button class="text-button" data-action="clear-log-filters">清除筛选</button></div>`;
    const levels = { info: '信息', success: '已确认', warning: '注意', error: '异常' };
    return logs.map((entry) => `<div class="log-row"><time class="log-time">${esc(entry.time)}</time><span class="log-level ${entry.level}">${levels[entry.level] || '信息'}</span><span class="log-account">${esc(entry.account)}</span><div class="log-message"><p>${esc(entry.message)}</p><span class="log-event">${esc(entry.event)}</span></div></div>`).join('');
  }

  function applyNativeLabels() {
    if (!nativeMode) return;
    const scope = main.querySelector('.summary-scope span');
    if (scope) scope.textContent = '开始时核实当前游戏窗口与账号，只执行本轮选择。';
    const requestButton = main.querySelector('[data-action="show-request"]');
    if (requestButton) requestButton.innerHTML = `${icon('code')}查看任务参数`;
    const exportButton = main.querySelector('[data-action="export-logs"]');
    if (exportButton) exportButton.innerHTML = `${icon('download')}导出当前日志`;
    const count = main.querySelector('#log-count');
    if (count) count.textContent = `${filteredLogs().length} / ${state.snapshot?.logs.length || 0} 条 · 最近日志，完整记录见日志目录`;
    main.querySelectorAll('.card-header p').forEach(order => {
      if (order.textContent.includes('SOLO_OPTIONS')) order.textContent = '任务按固定流程顺序执行。';
    });
    const safety = main.querySelector('.safety-note span');
    if (safety && !safety.textContent.startsWith('配置待修正')) safety.textContent = '完成须核实任务、领奖与回村；购买始终禁用。运行中可停止本轮或按F12。';
    const progressNote = main.querySelector('.progress-toolbar p');
    if (progressNote) progressNote.textContent = '点击任务单元格查看次数、未决动作、回村与图片证据。';
    const footer = document.querySelector('.sidebar-version');
    if (footer && N.info?.version) footer.textContent = N.info.version;
    const version = document.querySelector('.version-chip');
    if (version && N.info?.version) version.textContent = 'V '+N.info.version;
  }

  async function nativeCommand(method) {
    try {
      if (method === 'check_window') state.lastCheck = '';
      if (method === 'detect_leaders') state.lastLeaders = '';
      const result = await N.call(method);
      if (!result.ok) { toast(result.error || '当前操作未执行。', 'warning'); return; }
      if (result.path) toast('已保存：'+result.path);
      if (result.warning) toast(result.warning, 'warning');
      await N.refresh(true);
    } catch (error) { toast(error.message || String(error), 'warning'); }
  }

  async function nativeStart() {
    if (state.nativePending || !N.ready || N.info?.busy) return;
    state.nativePending = true;
    render();
    try {
      const result = await N.call('start',D.requestFor(state.config));
      if (!result.ok) { toast(result.error || '本轮尚未开始。', 'warning'); return; }
      state.config.newBondRound = false;
      state.page = 'progress';
      state.accountFilter = 'all';
      state.logClearedId = 0;
      main.scrollTop = 0;
    } catch (error) { toast(error.message || String(error), 'warning'); }
    finally {
      state.nativePending = false;
      await N.refresh(true);
      render();
    }
  }

  function configFromRequest(request) {
    const config = D.freshConfig();
    const mode = request.mode;
    config.section = ['solo','multi'].includes(mode) ? 'daily' : ['team','team_then_multi'].includes(mode) ? 'team' : 'independent';
    config.scope = mode === 'multi' ? 'multi' : 'current';
    config.teamMode = mode === 'team_then_multi' ? 'pipeline' : 'auto';
    config.independent = config.section === 'independent' ? mode : config.independent;
    const pairs = {tasks:'tasks',account_count:'accountCount',arena_wins:'arenaWins',formation:'formation',
      leader:'leader',members:'members',friend_shortcut:'friendShortcut',bond_count:'bondCount',
      new_bond_round:'newBondRound',final_managed:'finalManaged',arena_target_mode:'arenaTargetMode'};
    for (const [source,target] of Object.entries(pairs)) if (request[source] !== undefined) config[target] = request[source];
    config.pipelineAuto = !request.leaders?.length;
    config.pipelineLeaders = request.leaders?.length ? request.leaders.slice(0,2) : [1,6];
    if (mode === 'team_then_multi') config.pipelineTasks = request.team_tasks;
    else config.teamTasks = request.team_tasks || config.teamTasks;
    config.newBondRound = false; // The explicit new-round request is consumed once.
    return config;
  }

  if (nativeMode) {
    window.addEventListener('hyol-native-state', ({detail:info}) => {
      if (info.snapshot?.runId && state.nativeRunId !== info.snapshot.runId) {
        state.nativeRunId = info.snapshot.runId;
        if (info.request) state.config = configFromRequest(info.request);
        state.logClearedId = 0;
      }
      const snapshot = info.snapshot ? {...info.snapshot,plan:D.planFor(state.config)} : null;
      if (snapshot) snapshot.logs = snapshot.logs.filter(row => row.id > state.logClearedId);
      state.snapshot = snapshot;
      state.scenario = snapshot ? 'native' : 'idle';
      const note = document.querySelector('.demo-notice-text');
      if (note) note.textContent = info.detail || '本地运行层已连接；点击开始后执行所选任务';
      const connection = document.querySelector('.connection-card div span');
      if (connection) connection.textContent = info.busy ? '本轮正在处理' : '运行层已连接';
      render();
      if (info.close_notice && info.close_notice !== state.closeNotice) {
        state.closeNotice = info.close_notice;
        toast(info.close_notice,'warning');
      }
      const check = JSON.stringify(info.last_check);
      if (info.last_check && check !== state.lastCheck) {
        state.lastCheck = check;
        const got = info.last_check;
        showDialog('help',`${dialogHeader('只读窗口检查', '实际运行层返回的检查结果')}<div class="dialog-body"><div class="detail-grid"><div class="detail-item"><span>状态</span><strong>${esc(got.status || '待核实')}</strong></div><div class="detail-item"><span>窗口健康</span><strong>${got.healthy === true ? '是' : '尚未确认'}</strong></div><div class="detail-item"><span>主界面</span><strong>${got.main_ready === true ? '已确认' : '尚未确认'}</strong></div><div class="detail-item"><span>内容点击数</span><strong>${esc(got.content_clicks ?? '未知')}</strong></div></div><div class="reason-box" style="margin-top:16px">${esc(got.reason || info.detail)}</div></div>`);
      }
      const leaders = JSON.stringify(info.leaders);
      if (info.leaders && leaders !== state.lastLeaders) {
        state.lastLeaders = leaders;
        const slots = info.leaders.leaders || [];
        if (slots.length) state.config.pipelineLeaders = slots.slice(0,2);
        state.detectedLeaders = slots.length ? '已核实队长标签：'+slots.join('、') : '未核实到队长，请先在游戏组成队伍。';
        render();
        toast(state.detectedLeaders,slots.length ? 'info' : 'warning');
      }
    });
    window.addEventListener('hyol-native-error',({detail}) => {
      const note = document.querySelector('.demo-notice-text');
      if (note) note.textContent = '界面连接待恢复：'+detail;
    });
  }

  function refreshLogList() {
    const stream = document.querySelector('.log-stream');
    if (stream) stream.innerHTML = renderLogRows();
    const count = document.getElementById('log-count');
    if (count) count.textContent = `${filteredLogs().length} / ${state.snapshot?.logs.length || 0} 条 · 仅界面演示数据`;
  }

  function showDialog(kind, content) {
    dialog.dataset.kind = kind;
    dialogContent.innerHTML = content;
    if (!dialog.open) dialog.showModal();
  }
  function closeDialog() {
    if (dialog.open) dialog.close();
    state.detailSelection = null;
    if (nativeMode) dialogContent.replaceChildren();
  }
  function dialogHeader(title, subtitle) {
    return `<div class="dialog-header"><div><h2 id="dialog-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="dialog-close" data-action="close-dialog" aria-label="关闭">${icon('close')}</button></div>`;
  }

  function showDetail(slot, code) {
    const account = state.snapshot?.accounts.find((item) => item.slot === Number(slot));
    if (!account) return;
    const rec = account.tasks[code];
    state.detailSelection = { slot: Number(slot), code };
    const labels = {
      completed: '已核实轮次 / 次数', verified_battles: '已核实战斗总数', verified_first_round_battles: '已归档首轮战斗',
      verified_current_round_battles: '当前轮战斗', remaining: '剩余次数', verified_wins: '已确认新增胜场', requested_wins: '目标胜场',
      phase: '当前阶段', requested: '目标场数', finished_at: '完成时间',
    };
    const details = Object.entries(labels).filter(([key]) => rec[key] !== undefined).map(([key, label]) => `<div class="detail-item"><span>${label}</span><strong>${esc(rec[key])}</strong></div>`).join('');
    const events = (state.snapshot.logs || []).filter((entry) => entry.account === account.label || (account.label === '当前窗口' && entry.account === '系统')).slice(-4).reverse();
    const evidence = rec.evidence_paths || [];
    showDialog('detail', `${dialogHeader(`${account.label} · ${D.taskLabel(code)}`, `${account.identity}${nativeMode ? ' · 本轮核实记录' : ' · 界面演示数据'}`)}<div class="dialog-body"><div class="dialog-section"><div style="margin-bottom:13px">${statusBadge(rec.status, state.snapshot.status === 'not_open' && rec.status === 'skipped' ? '活动未开放' : '')}</div><div class="detail-grid"><div class="detail-item"><span>任务完成确认</span><strong>${rec.task_completed === true ? '是' : '否 / 尚未核实'}</strong></div><div class="detail-item"><span>回村确认</span><strong>${code === 'managed' ? '不适用' : rec.home_returned === true ? '是' : '否 / 尚未核实'}</strong></div>${details}</div></div>
      ${rec.last_error ? `<div class="dialog-section"><h3>待处理原因</h3><div class="reason-box">${esc(rec.last_error)}</div></div>` : ''}
      ${rec.pending_actions?.length ? `<div class="dialog-section"><h3>未决动作 · 禁止盲目重复</h3><div class="reason-box mono">${rec.pending_actions.map(esc).join('<br>')}</div></div>` : ''}
      <div class="dialog-section"><h3>已核实记录</h3><div class="detail-grid"><div class="detail-item"><span>结果记录</span><strong>${rec.verified_results?.length || 0} 条</strong></div><div class="detail-item"><span>领奖记录</span><strong>${rec.verified_claims?.length || 0} 条</strong></div></div></div>
      <div class="dialog-section"><h3>最近明细</h3>${events.length ? `<div class="event-timeline">${events.map((entry) => `<div class="event-item"><time>${esc(entry.time)}</time>${esc(entry.message)}</div>`).join('')}</div>` : '<p class="muted small">该环节尚无事件明细，保持未执行状态。</p>'}</div>
      <div class="dialog-section"><h3>本地证据</h3>${evidence.length ? `${nativeMode ? `<p class="muted small">本轮已保存 ${evidence.length} 张图片证据。</p>` : '<img class="evidence-thumb" src="assets/demo-evidence.svg" alt="界面演示证据示意图，非真实游戏截图">'}<div class="dialog-actions"><button class="button button-secondary button-small" data-action="open-evidence">${icon('eye')}${nativeMode ? '查看图片证据' : '查看演示证据'}</button></div>` : '<p class="muted small">该环节尚无可打开的截图；原状态保留，不会伪造完成凭证。</p>'}</div></div>`);
  }

  async function showEvidence() {
    const selection = state.detailSelection;
    const account = state.snapshot?.accounts.find((item) => item.slot === Number(selection?.slot));
    const rec = account?.tasks[selection?.code];
    const path = rec?.evidence_paths?.at(-1) || 'demo://evidence/unavailable.svg';
    if (nativeMode) {
      try {
        const result = await N.call('read_evidence', path);
        if (!result.ok) { toast(result.error, 'warning'); return; }
        showDialog('evidence', `${dialogHeader('本轮图片证据', '按运行层已记录路径只读查看')}<div class="dialog-body"><img class="evidence-thumb" src="${esc(result.data_url)}" alt="本轮运行已保存的图片证据"><div class="detail-item"><span>本地图片路径</span><strong class="mono">${esc(result.path)}</strong></div><div class="dialog-actions"><button class="button button-secondary" data-action="back-to-detail">返回任务明细</button><button class="button button-primary" data-action="close-dialog">关闭</button></div></div>`);
      } catch (error) { toast(error.message, 'warning'); }
      return;
    }
    showDialog('evidence', `${dialogHeader('演示证据查看器', '仅验证查看交互，不是任务完成凭证')}<div class="dialog-body"><img class="evidence-thumb" src="assets/demo-evidence.svg" alt="明确标注为界面演示证据的示意图"><div class="reason-box" style="margin-top:14px"><strong>界面演示数据</strong><br>此图是设计用矢量示意，不是游戏截图，不代表账号核验、任务完成、领奖或回村。正式版应通过现有证据路径只读打开 PNG / JPG。</div><div class="detail-item" style="margin-top:14px"><span>演示证据标识</span><strong class="mono">${esc(path)}</strong></div><div class="dialog-actions">${selection ? '<button class="button button-secondary" data-action="back-to-detail">返回任务明细</button>' : ''}<button class="button button-primary" data-action="close-dialog">关闭</button></div></div>`);
  }

  function showHelp() {
    if (nativeMode) {
      showDialog('help', `${dialogHeader('使用说明', '本地桌面客户端')}<div class="dialog-body"><div class="help-lead">先在任务计划中选择账号范围、任务和参数，再点击开始。本轮进度与日志由现有任务运行层更新；完成必须有任务、领奖和回村核实。</div><div class="dialog-section"><h3>运行与停止</h3><p>点击开始会操作真实游戏。请先打开游戏并准备好需要的标签；运行中可点击停止或按F12。收到停止后等待本轮结束，再关闭窗口。</p></div><div class="dialog-section"><h3>状态与证据</h3><p>单个账号掉线不代表其他账号停止。待核实、未开放和跳过均不等于完成。进度读取失败会保留上次有效快照；图片只按本轮已记录路径查看。</p></div><div class="dialog-section"><h3>日志与配置</h3><p>界面显示最近1000条日志，完整记录保留在日志目录。导出保存当前日志副本；清空显示不会删除磁盘记录。实际购买保持禁用。</p></div></div>`);
      return;
    }
    showDialog('help', `${dialogHeader('使用说明与安全边界', '新界面原型 · 仅界面演示数据')}<div class="dialog-body"><div class="help-lead">主流程是“任务计划 → 执行清单 → 本轮进度”。所有演示按钮只改变浏览器内的模拟状态，不导入原运行层，也不读取游戏、INI、个人日志或断点。</div><div class="help-list"><div class="help-item"><span>1</span><div><strong>先确认范围，再开始</strong><p>当前账号或最多六个标签、两种组队流程、四种独立任务都在任务计划中。右侧清单按真实顺序列出任务与关键参数。</p></div></div><div class="help-item"><span>2</span><div><strong>开始 / 停止只控制演示</strong><p>开始会创建新演示运行并推进模拟状态；停止会先显示“停止中”，再转为“已停止”。浏览器中可按 F12 尝试同样的演示停止，正式全局 F12 仍须由原运行层实现。</p></div></div><div class="help-item"><span>3</span><div><strong>状态必须带原因与证据</strong><p>点击任务单元格可查看完成、回村、未决动作和证据。掉线、待核实、活动未开放与刷新失败均不等于完成；刷新失败保留上次有效快照。</p></div></div><div class="help-item"><span>4</span><div><strong>日志工具</strong><p>支持全文搜索、级别与账号筛选、自动滚动和导出演示文本。“清空显示”不删除磁盘日志；演示模式不会打开个人日志目录。</p></div></div></div><div class="dialog-section" style="margin-top:18px"><h3>保持不变的现有边界</h3><ul class="boundary-list"><li>账号身份、任务完成、领奖与回村仍须真实核实。</li><li>F12 紧急停止、购买禁用、识别阈值、INI 与断点格式不在 UI 重写。</li><li>当前产品只展示本轮进度，不提供旧任务恢复、批次切换或历史累计。</li><li>原型未连接真实 RuntimeController，未进行实机验收。</li></ul></div></div>`);
  }

  function showWindowCheck() {
    if (nativeMode) { nativeCommand('check_window'); return; }
    showDialog('help', `${dialogHeader('只读窗口检查 · 演示结果', '没有读取或点击任何真实窗口')}<div class="dialog-body"><div class="help-lead">以下仅展示正式检查结果的界面呈现方式，不代表当前电脑通过预检。</div><div class="dialog-section" style="margin-top:16px"><div class="detail-grid"><div class="detail-item"><span>检查状态</span><strong style="color:var(--success)">演示：ready</strong></div><div class="detail-item"><span>窗口健康</span><strong>演示：是</strong></div><div class="detail-item"><span>主界面确认</span><strong>演示：通过</strong></div><div class="detail-item"><span>内容点击数</span><strong>0</strong></div></div></div><div class="reason-box" style="margin-top:16px">正式对接调用 RuntimeController.check_window()；运行中保持禁用，返回 unified_window_check 后再显示真实结果。此演示没有执行该调用。</div><div class="dialog-actions"><button class="button button-primary" data-action="close-dialog">知道了</button></div></div>`);
  }

  function showRequest() {
    const request = D.requestFor(state.config);
    showDialog('help', `${dialogHeader(nativeMode ? '本轮任务参数' : '演示 RunRequest', nativeMode ? '开始时由现有运行层再次校验' : '只生成预览，不提交给 RuntimeController')}<div class="dialog-body"><pre class="request-preview">${esc(JSON.stringify(request, null, 2))}</pre><div class="dialog-actions"><button class="button button-primary" data-action="close-dialog">关闭</button></div></div>`);
  }

  function startDemo() {
    const error = D.validate(state.config);
    if (error) {
      state.page = 'plan';
      state.advancedOpen = true;
      render();
      toast(error, 'warning');
      return;
    }
    if (nativeMode) { nativeStart(); return; }
    clearTimers();
    state.snapshot = D.makeSnapshot(state.config, 'fresh');
    state.snapshot.runId = `DEMO-${Date.now().toString().slice(-8)}`;
    state.snapshot.startedAt = nowText();
    state.snapshot.updatedAt = state.snapshot.startedAt;
    state.snapshot.status = 'running';
    state.snapshot.logs = [
      { time: clockText(), level: 'info', account: '系统', event: 'unified_run_started', message: `已创建演示运行 ${state.snapshot.runId}；${state.snapshot.plan.title}。` },
      { time: clockText(), level: 'success', account: '系统', event: 'unified_window_check', message: '演示只读预检通过；未检查或点击真实游戏窗口。' },
    ];
    const current = findCurrent('running');
    if (current) {
      state.snapshot.logs.push({ time: clockText(), level: 'info', account: current.account.label, event: 'multi_solo_task_started', message: `${D.taskLabel(current.code)}开始（演示推进，不代表真实调度时序）。` });
    }
    state.scenario = 'running';
    state.userRun = true;
    state.config.newBondRound = false;
    state.page = 'progress';
    state.accountFilter = 'all';
    render();
    toast('已创建新的演示运行；没有调用真实任务执行器。');
    state.autoTimer = setInterval(advanceDemo, 2400);
  }

  function advanceDemo() {
    if (nativeMode) return;
    const snapshot = state.snapshot;
    if (!snapshot || snapshot.status !== 'running') {
      clearInterval(state.autoTimer);
      return;
    }
    const current = findCurrent('running');
    if (current) {
      current.account.tasks[current.code] = D.record('done', current.code);
      snapshot.logs.push({ time: clockText(), level: 'success', account: current.account.label, event: current.code === 'managed' ? 'multi_solo_managed_complete' : 'multi_solo_task_complete', message: `${D.taskLabel(current.code)}任务、结果与所需收尾已确认（界面演示数据）。` });
    }
    let next = null;
    for (const account of snapshot.accounts) {
      const code = snapshot.taskCodes.find((item) => account.tasks[item].status === 'new');
      if (code) { next = { account, code }; break; }
    }
    if (next) {
      next.account.tasks[next.code] = D.record('running', next.code);
      snapshot.logs.push({ time: clockText(), level: 'info', account: next.account.label, event: 'multi_solo_task_started', message: `${D.taskLabel(next.code)}开始（演示）。` });
    } else {
      snapshot.status = 'complete';
      state.scenario = 'complete';
      clearInterval(state.autoTimer);
      state.autoTimer = null;
      snapshot.logs.push({ time: clockText(), level: 'success', account: '系统', event: 'unified_finished', message: `本轮演示已结束；所选任务与最后收尾均显示为已确认。` });
      toast('演示运行已到达已完成状态。');
    }
    snapshot.updatedAt = nowText();
    render();
    if (state.page === 'logs' && state.autoScroll) main.scrollTop = main.scrollHeight;
  }

  function stopDemo(source = 'button') {
    if (nativeMode) { nativeCommand('stop'); return; }
    if (state.snapshot?.status !== 'running') return;
    clearInterval(state.autoTimer);
    state.autoTimer = null;
    state.snapshot.status = 'stopping';
    state.scenario = 'stopping';
    state.snapshot.logs.push({ time: clockText(), level: 'warning', account: '系统', event: 'unified_stop_requested', message: source === 'F12' ? '收到演示 F12：正在紧急结束本轮，保留断点。' : '停止已请求：正在结束本轮，保留断点。' });
    render();
    state.stopTimer = setTimeout(() => {
      if (!state.snapshot) return;
      state.snapshot.accounts.forEach((account) => state.snapshot.taskCodes.forEach((code) => {
        if (account.tasks[code].status === 'running') account.tasks[code] = D.record('pending', code, { last_error: '运行已停止，完成条件尚未核实；未重复提交原动作。' });
      }));
      state.snapshot.status = 'stopped';
      state.snapshot.updatedAt = nowText();
      state.scenario = 'stopped';
      if (source === 'F12') state.snapshot.logs.push({ time: clockText(), level: 'warning', account: '系统', event: 'emergency_stop', message: 'F12 停止演示，保留断点；未操作真实游戏。' });
      state.snapshot.logs.push({ time: clockText(), level: 'warning', account: '系统', event: 'unified_finished', message: '本轮已停止；再次开始将按当前画面和选择创建新运行。' });
      render();
      toast('演示已停止；未核实内容保留为待处理。', 'warning');
    }, 1800);
  }

  function loadScenario(kind, quiet = false) {
    if (nativeMode) return;
    clearTimers();
    state.config = D.scenarioConfig(kind);
    state.scenario = kind;
    state.snapshot = kind === 'idle' ? null : D.makeSnapshot(state.config, kind);
    state.page = kind === 'idle' ? 'plan' : 'progress';
    state.userRun = false;
    state.accountFilter = 'all';
    state.logAccount = 'all';
    state.logSearch = '';
    state.logLevel = 'all';
    closeDialog();
    render();
    if (!quiet && kind !== 'idle') toast(`已载入“${scenarioSelect.querySelector(`option[value="${kind}"]`)?.textContent || kind}”演示状态。`);
  }

  function exportLogs() {
    if (nativeMode) { nativeCommand('export_logs'); return; }
    const lines = filteredLogs().map((entry) => `${entry.time}\t${entry.level}\t${entry.account}\t${entry.event}\t${entry.message}`);
    const content = ['火影 OL 客户端界面原型 · 仅界面演示数据', `导出时间：${nowText()}`, '', ...lines].join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff', content], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `hyol-demo-${Date.now()}.log.txt`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`已导出 ${lines.length} 条演示日志；未读取个人日志。`);
  }

  document.addEventListener('click', (event) => {
    const pageButton = event.target.closest('[data-page]');
    if (pageButton) {
      state.page = pageButton.dataset.page;
      render();
      main.scrollTop = 0;
      return;
    }
    const sectionButton = event.target.closest('[data-section]');
    if (sectionButton) {
      state.config.section = sectionButton.dataset.section;
      render();
      return;
    }
    const teamMode = event.target.closest('[data-team-mode]');
    if (teamMode) {
      state.config.teamMode = teamMode.dataset.teamMode;
      render();
      return;
    }
    const independent = event.target.closest('[data-independent]');
    if (independent) {
      state.config.independent = independent.dataset.independent;
      render();
      return;
    }
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === 'help') showHelp();
    if (action === 'check-window') showWindowCheck();
    if (action === 'close-dialog') closeDialog();
    if (action === 'show-request') showRequest();
    if (action === 'start') startDemo();
    if (action === 'stop') stopDemo();
    if (action === 'reset-demo') { clearTimers(); loadScenario('idle', true); state.logSearch = ''; toast('演示已恢复默认设置。'); }
    if (action === 'account-minus' || action === 'account-plus') {
      state.config.accountCount = Math.max(1, Math.min(6, Number(state.config.accountCount) + (action === 'account-plus' ? 1 : -1)));
      render();
    }
    if (action === 'toggle-member') {
      const slot = Number(button.dataset.slot);
      const selected = state.config.members.includes(slot);
      if (!selected && state.config.members.length >= 2) { toast('最多选择两名队员；请先取消一名。', 'warning'); return; }
      state.config.members = selected ? state.config.members.filter((item) => item !== slot) : [...state.config.members, slot].sort((a, b) => a - b);
      render();
    }
    if (action === 'clear-tasks' || action === 'default-tasks') {
      const group = button.dataset.taskGroup;
      const key = group === 'solo' ? 'tasks' : group === 'team' ? 'teamTasks' : 'pipelineTasks';
      const source = group === 'solo' ? D.SOLO_TASKS : group === 'team' ? D.TEAM_TASKS : D.PIPELINE_TASKS;
      state.config[key] = action === 'clear-tasks' ? [] : source.filter((task) => task.default).map((task) => task.code);
      render();
    }
    if (action === 'detect-leaders') {
      if (nativeMode) { nativeCommand('detect_leaders'); return; }
      state.config.pipelineLeaders = [1, 6];
      state.detectedLeaders = '演示识别结果：队长标签 1、6；内容点击数 0。未读取真实窗口。';
      render();
      toast('已填入演示队长标签；未执行真实识别。');
    }
    if (action === 'open-detail') showDetail(button.dataset.account, button.dataset.task);
    if (action === 'open-evidence') showEvidence();
    if (action === 'back-to-detail' && state.detailSelection) showDetail(state.detailSelection.slot, state.detailSelection.code);
    if (action === 'refresh-progress') {
      if (nativeMode) { N.refresh(true); return; }
      if (!state.snapshot) { toast('尚未开始本轮，暂无可刷新的演示进度。', 'warning'); return; }
      const recovered = Boolean(state.snapshot.warning);
      state.snapshot.warning = '';
      state.snapshot.updatedAt = nowText();
      if (state.scenario === 'refresh_failed') state.scenario = 'running';
      state.snapshot.logs.push({ time: clockText(), level: 'success', account: '系统', event: 'progress_refreshed', message: recovered ? '演示索引已恢复；沿用上次有效快照，未伪造新增完成项。' : '已刷新当前演示快照；没有读取个人运行资料。' });
      render();
      toast(recovered ? '演示刷新已恢复，原核实数据保持不变。' : '已刷新本轮演示进度。');
    }
    if (action === 'clear-account-filter') { state.accountFilter = 'all'; render(); }
    if (action === 'export-logs') exportLogs();
    if (action === 'clear-logs') {
      if (state.snapshot) {
        if (nativeMode) state.logClearedId = Math.max(state.logClearedId,...state.snapshot.logs.map(row => row.id || 0));
        state.snapshot.logs = [];
      }
      render(); toast(nativeMode ? '已清空当前日志显示，磁盘记录保留。' : '仅清空了当前演示日志显示，未删除任何磁盘文件。');
    }
    if (action === 'clear-log-filters') { state.logSearch = ''; state.logLevel = 'all'; state.logAccount = 'all'; render(); }
    if (action === 'open-log-directory') {
      if (nativeMode) nativeCommand('open_logs');
      else toast('演示模式不读取或打开个人日志目录；正式接入后保留此入口。', 'warning');
    }
  });

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target === scenarioSelect) { loadScenario(target.value); return; }
    if (target.id === 'account-filter') { state.accountFilter = target.value; render(); return; }
    if (target.id === 'log-level') { state.logLevel = target.value; refreshLogList(); return; }
    if (target.id === 'log-account') { state.logAccount = target.value; refreshLogList(); return; }
    if (target.id === 'auto-scroll') { state.autoScroll = target.checked; return; }
    if (target.dataset.pipelineLeader !== undefined) {
      state.config.pipelineLeaders[Number(target.dataset.pipelineLeader)] = target.value ? Number(target.value) : '';
      render();
      return;
    }
    if (target.dataset.taskCode) {
      const group = target.dataset.taskGroup;
      const key = group === 'solo' ? 'tasks' : group === 'team' ? 'teamTasks' : 'pipelineTasks';
      const code = target.dataset.taskCode;
      const selected = state.config[key].includes(code);
      state.config[key] = target.checked ? [...state.config[key], code] : state.config[key].filter((item) => item !== code);
      if (selected === target.checked) return;
      render();
      return;
    }
    if (target.dataset.config) {
      const key = target.dataset.config;
      let value = target.type === 'checkbox' ? target.checked : target.value;
      if (['accountCount', 'arenaWins', 'bondCount', 'leader'].includes(key)) value = Number(value);
      state.config[key] = value;
      if (key === 'leader') state.config.members = state.config.members.filter((slot) => slot !== value);
      render();
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.id === 'log-search') {
      state.logSearch = event.target.value;
      refreshLogList();
    }
  });
  document.addEventListener('toggle', (event) => {
    if (event.target.matches('[data-advanced]')) state.advancedOpen = event.target.open;
  }, true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'F12' && (nativeMode ? N.info?.busy : state.snapshot?.status === 'running')) {
      event.preventDefault();
      stopDemo('F12');
    }
    if (event.key === 'Escape' && dialog.open) closeDialog();
  });
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
    }
  });

  const params = new URLSearchParams(location.search);
  const initialScenario = nativeMode ? null : params.get('scenario');
  if (initialScenario && [...scenarioSelect.options].some((option) => option.value === initialScenario)) {
    loadScenario(initialScenario, true);
  } else {
    render();
  }
  if (!nativeMode && params.get('section')) {
    state.config.section = params.get('section');
    if (params.get('teamMode')) state.config.teamMode = params.get('teamMode');
    if (params.get('independent')) state.config.independent = params.get('independent');
    state.page = 'plan';
    state.scenario = 'idle';
    state.snapshot = null;
    render();
  }
  if (!nativeMode && ['plan', 'progress', 'logs'].includes(params.get('page'))) {
    state.page = params.get('page');
    render();
  }
  if (!nativeMode && params.get('detail') === 'pending') {
    const current = findCurrent('pending');
    if (current) showDetail(current.account.slot, current.code);
  }
})();
