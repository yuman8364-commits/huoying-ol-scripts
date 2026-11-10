(() => {
  const D = window.HYOL_DEMO;
  const N = window.HYOL_NATIVE;
  const nativeMode = Boolean(N?.enabled);
  if (nativeMode) Object.assign(D.RUN_STATUS, {
    starting: { label: '启动中', title: '已收到开始，正在启动', tone: 'info' },
    checking: { label: '检查中', title: '正在检查并回木叶村', tone: 'info' },
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
    logTask: 'all',
    taskSettings: '',
    autoScroll: true,
    detailSelection: null,
    nativePending: false,
    nativeStartToken: 0,
    nativeRunId: '',
    logClearedId: 0,
    lastCheck: '',
    lastLeaders: '',
    closeNotice: '',
    settingsLoaded: !nativeMode,
    settingsSaving: false,
    onboarding: null,
    tour: null,
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
    const focus = nativeMode && document.hasFocus() && active?.id === 'log-search'
      ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
    const oldScroll = main.scrollTop;
    const oldQueueScroll = main.querySelector('.queue-scroll')?.scrollTop || 0;
    const logSequence=Math.max(0,...allLogs().map(row=>row.id||0));
    const newLogs=state.renderedLogSequence!==logSequence;state.renderedLogSequence=logSequence;
    const oldTableScroll = main.querySelector('.table-wrap')?.scrollLeft || 0;
    renderHeader();
    renderNav();
    renderMain();
    const queueScroll = main.querySelector('.queue-scroll');
    if (queueScroll) queueScroll.scrollTop = oldQueueScroll;
    renderDock();
    renderActivityRail();
    applyNativeLabels();
    scenarioSelect.value = state.scenario;
    if (nativeMode) {
      if (state.page === 'plan' && (N.info?.busy || state.nativePending || !state.settingsLoaded || state.settingsSaving)) {
        main.querySelectorAll('input,select,button,summary').forEach((e) => {
          if (e.tagName !== 'SUMMARY') e.disabled = true;
        });
      }
      main.scrollTop = state.page==='logs' && state.autoScroll && newLogs ? main.scrollHeight : oldScroll;
      const table = main.querySelector('.table-wrap');
      if (table) table.scrollLeft = oldTableScroll;
      if (focus && document.hasFocus()) {
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
    const busy = nativeMode ? (!N.ready || N.info?.busy || state.nativePending || !state.settingsLoaded || state.settingsSaving) : ['running', 'stopping'].includes(state.snapshot?.status);
    document.querySelector('[data-action="check-window"]').disabled = busy;
    document.querySelector('[data-action="save-settings"]').disabled = busy;
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
    if(state.tour){const html=renderTour();if(main.dataset.tourHtml!==html){main.innerHTML=html;main.dataset.tourHtml=html;}delete main.dataset.planHtml;return;}else delete main.dataset.tourHtml;
    if (state.page === 'plan') { const html=renderPlan(),key=html+Boolean(N?.info?.busy||state.nativePending||!state.settingsLoaded||state.settingsSaving); if(main.dataset.planHtml!==key){main.innerHTML=html;main.dataset.planHtml=key;} }
    if (state.page === 'progress') {delete main.dataset.planHtml;main.innerHTML = renderProgress();}
    if (state.page === 'logs') {delete main.dataset.planHtml;const html=renderLogs();if(main.dataset.logsHtml!==html){main.innerHTML=html;main.dataset.logsHtml=html;}} else delete main.dataset.logsHtml;
  }

  function renderDock() {
    const snapshot = state.snapshot;
    const status = nativeMode && state.nativePending ? 'starting' : nativeMode && N.info?.busy && N.info.operation !== 'run'
      ? (N.info.operation === 'leaders' ? 'leaders' : 'checking') : snapshot?.status || 'idle';
    const meta = runMeta(status);
    const plan = D.planFor(state.config);
    let title = meta.title;
    let subtitle = !snapshot || state.scenario === 'idle'
      ? `${plan.title} · ${plan.taskCodes.length} 个执行环节`
      : `${snapshot.runId}${nativeMode ? '' : ' · 界面演示数据'}`;
    if (status === 'starting') subtitle = `${plan.title} · 正在交接游戏窗口并核对当前账号`;
    if (status === 'running') {
      const current = findCurrent('running') || findCurrent('pending') || findCurrent('offline');
      if (current) subtitle = `${current.account.label} · ${D.taskLabel(current.code)} · ${current.account.identity}`;
      else if (nativeMode && N.info?.detail) subtitle = N.info.detail;
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
    else if (nativeMode && (!state.settingsLoaded || state.settingsSaving)) controls = '<button class="button button-primary button-large" disabled>'+ (state.settingsSaving ? '正在保存设置…' : '正在读取保存设置…')+'</button>';
    if(!active && (state.tour || state.onboarding?.required))controls='<button class="button button-primary button-large" data-action="onboarding">查看或跳过设置引导</button>';
    dock.innerHTML = `<div class="dock-status"><span class="dock-status-icon tone-${meta.tone}">${icon(active ? 'clock' : status === 'complete' ? 'check' : status === 'idle' ? 'plan' : 'alert')}</span><div class="dock-copy"><strong>${esc(title)}</strong><span>${esc(subtitle)}</span></div></div><div class="dock-actions">${controls}</div>`;
  }

  function renderPlan() {
    const section = state.config.section;
    return `<div class="workstation">${renderQueue()}<div class="plan-layout queue-settings"><div class="plan-editor">
      <div class="mode-switch plan-modes" role="tablist" aria-label="任务组织方式">
        ${modeCard('daily', 'user', '单人 / 多账号', '当前窗口或最多六个标签')}
        ${modeCard('team', 'team', '组队任务', '自动建队或衔接已有队伍')}
        ${modeCard('independent', 'arena', '独立任务', '拼图、修行录及其他独立任务')}
        ${modeCard('anniversary', 'weekend', '周年庆活动', '周年庆签到及本期相关内容')}
      </div>
      ${section === 'daily' ? renderDaily() : section === 'team' ? renderTeam() : section === 'anniversary' ? renderAnniversary() : renderIndependent()}
    </div>${renderSummary()}</div></div>`;
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
    const group=config.scope==='current'?'current':'solo';
    const tools=taskTools(group);
    const training = config.dailyMode === 'training_record';
    const modes = `<section class="card">${cardHeader('01','单人任务方式','可自选日常，也可按修行录自动识别缺项。')}<div class="card-body"><div class="mode-switch" aria-label="单人任务方式"><button class="mode-card ${!training ? 'is-active' : ''}" data-daily-mode="manual"><span class="mode-icon">${icon('plan')}</span><span class="mode-copy"><strong>自选日常</strong><span>勾选任务，自行安排执行顺序</span></span></button><button class="mode-card ${training ? 'is-active' : ''}" data-daily-mode="training_record"><span class="mode-icon">${icon('check')}</span><span class="mode-copy"><strong>按修行录补任务</strong><span>先领完奖励，再为每个账号安排日常</span></span></button></div></div></section>`;
    const selection = training ? `<section class="card training-mode-card">${cardHeader('03','修行录自动补任务','每个账号先领完奖励，重新识别当前缺项后安排日常。')}<div class="card-body"><div class="phase-flow"><div class="phase-card"><strong>1 · 领完可领奖励</strong><span>遍历各页，领取已达成的目标奖励</span></div><div class="phase-arrow">${icon('arrow')}</div><div class="phase-card"><strong>2 · 重新识别并安排</strong><span>按本号最新目标与缺口定制日常</span></div><div class="phase-arrow">${icon('arrow')}</div><div class="phase-card"><strong>3 · 逐项执行与更新</strong><span>连续完成可靠目标，仅在解锁边界复核</span></div></div><div class="inline-note">${icon('info')}<span>领奖结果待核实会暂停本号排任务；计数不明、未开放或尚未支持的目标保留原因。进度页显示各账号的日常计划。</span></div>${commonControls()}</div></section>` : `<section class="card">${cardHeader('03', '选择单人任务', '可在下方调整执行顺序；在线福利默认最后领取。', tools)}<div class="card-body">${renderTaskGrid(catalogFor(group), selectionFor(group), group)}${renderSoloParameters()}</div></section>`;
    return modes + `<section class="card">${cardHeader('02', '账号范围', '标签仅代表窗口位置；账号身份在正式运行时重新核实。')}
      <div class="card-body"><div class="choice-grid">
        ${scopeChoice('current', '当前账号', '只操作当前游戏窗口', 'user')}
        ${scopeChoice('multi', '多个账号', '按当前配置顺序处理 1～6 个标签', 'users')}
      </div>${config.scope === 'multi' ? renderAccountCount() : '<div class="inline-note">' + icon('info') + '<span>单窗口任务未确认身份时显示“待核验”，不会显示为在线。</span></div>'}</div></section>
      ${selection}`;
  }

  function renderAnniversary() {
    return `<section class="card">${cardHeader('01', '周年庆活动', '本期内容按游戏中的限时入口执行；未检测到时自动略过。')}<div class="card-body"><div class="phase-flow">${D.ANNIVERSARY_TASKS.map(task => `<div class="phase-card"><strong>${esc(task.label)}</strong><span>${esc(task.description)}</span></div>`).join('')}</div><div class="inline-note">${icon('clock')}<span>本期薪火签到活动截至 2026年11月25日。按游戏实际解锁进度继续领取，不固定停在第1天。</span></div></div></section>
      <section class="card">${cardHeader('02', '账号范围', '从第1个标签开始，逐个核实所选账号；只处理周年庆内容。')}<div class="card-body">${renderAccountCount()}<div class="fixed-rule" style="margin-top:14px">${icon('shield')}<div>已领取的奖励只复核；领取结果未知时保留原凭据，下次先核对。完成后回村，关闭最后托管。活动结束或入口不存在时静默略过。</div></div></div></section>`;
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

  function catalogFor(group) {
    return group==='current'?D.CURRENT_TASKS:group==='solo'?D.SOLO_TASKS:group==='team'?D.TEAM_TASKS:D.PIPELINE_TASKS;
  }
  function selectionFor(group) {
    return group==='current'?[...state.config.tasks,...state.config.currentTeamTasks]:state.config[group==='solo'?'tasks':group==='team'?'teamTasks':'pipelineTasks'];
  }
  function setSelection(group,selected) {
    if(group==='current') {
      state.config.tasks=selected.filter(code=>D.SOLO_TASKS.some(task=>task.code===code));
      state.config.currentTeamTasks=selected.filter(code=>D.CURRENT_TEAM_TASKS.some(task=>task.code===code));
    } else state.config[group==='solo'?'tasks':group==='team'?'teamTasks':'pipelineTasks']=selected;
    if(group==='current' && selected.some(code=>['bond','arena_3v3'].includes(code)))state.config.finalManaged=false;
  }
  function setOrder(group,order) {
    if(group==='current'){
      state.config.currentTaskOrder=order;
      state.config.taskOrder=state.config.taskOrder||D.defaultOrders();
      state.config.taskOrder.solo=order.filter(code=>D.SOLO_TASKS.some(task=>task.code===code));
      persistTaskOrder();
    }
    else {state.config.taskOrder=state.config.taskOrder||D.defaultOrders();state.config.taskOrder[group]=order;persistTaskOrder();}
  }

  function renderTaskGrid(tasks, selected, group) {
    const order = D.orderedCatalog(state.config, group, tasks).filter(code => selected.includes(code));
    return `<div class="task-grid">${tasks.map((task) => {
      const checked = selected.includes(task.code);
      return `<label class="task-card ${checked ? 'is-selected' : ''}"><input type="checkbox" data-task-group="${group}" data-task-code="${task.code}" ${checked ? 'checked' : ''}><span class="task-card-content"><span class="task-card-title">${task.label}${!task.default ? '<span class="mini-tag">默认不选</span>' : ''}</span><span class="task-card-description">${task.description}</span></span></label>`;
    }).join('')}</div><div class="task-order"><strong>执行顺序</strong><small>调整后用于下一次开始，当前运行按开始时的顺序执行。</small>${order.map((code,index) => `<div class="task-order-row"><span>${index+1}. ${esc(D.taskLabel(code))}</span><div><button type="button" class="text-button" data-action="task-up" data-task-group="${group}" data-task-code="${code}" aria-label="上移${esc(D.taskLabel(code))}" ${index===0?'disabled':''}>上移</button><button type="button" class="text-button" data-action="task-down" data-task-group="${group}" data-task-code="${code}" aria-label="下移${esc(D.taskLabel(code))}" ${index===order.length-1?'disabled':''}>下移</button></div></div>`).join('')}</div>`;
  }

  let orderLoad = null;
  let orderSave = Promise.resolve();
  let orderTimer = null;
  let pendingOrder = null;
  let orderRevision = 0;
  let settingsLoad = null;
  const withTimeout=(promise,ms,label)=>Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(new Error(label)),ms))]);
  function loadSavedSettings() {
    if (!nativeMode || state.settingsLoaded) return Promise.resolve();
    if (!settingsLoad) settingsLoad=withTimeout(N.call('get_client_settings'),3000,'读取保存设置超时。').then(result=>{
      if (!result.ok) throw new Error(result.error || '保存设置暂未读取。');
      if (result.saved) state.config={...D.freshConfig(),...D.clone(result.config),newBondRound:false,escortPriorSettled:false};
      if (result.warning) toast(result.warning,'warning');
      return tourCall('get_onboarding').then(guide=>{
        if(!guide?.ok)throw new Error(guide?.error||'新人引导记录未读取');
        state.onboarding=guide;
        if(guide.required)state.tour={index:Math.min(5,guide.steps.length)};
      });
    }).catch(error=>toast(error.message,'warning')).finally(()=>{state.settingsLoaded=true;render();});
    return settingsLoad;
  }
  async function saveSettings() {
    if (state.settingsSaving || (nativeMode && (!N.ready || N.info?.busy || state.nativePending))) return;
    state.settingsSaving=true;render();
    try {
      await loadSavedSettings();await loadTaskOrder();await flushTaskOrder();
      const config=D.clone(state.config);
      if (nativeMode) {
        const result=await withTimeout(N.call('save_client_settings',config),5000,'保存设置超时，请重试。');
        if (!result.ok) throw new Error(result.error || '设置未保存。');
      } else {
        config.newBondRound=false;config.escortPriorSettled=false;
        localStorage.setItem('hyol-desktop-settings',JSON.stringify(config));
      }
      toast('设置已保存，下次打开会自动恢复。');
    } catch (error) {toast(error.message || String(error),'warning');}
    finally {state.settingsSaving=false;render();}
  }
  function loadTaskOrder() {
    if (!nativeMode) return Promise.resolve();
    if (!orderLoad) {
      const revision=orderRevision;
      orderLoad=loadSavedSettings().then(()=>withTimeout(N.call('get_task_order'),3000,'读取任务顺序超时，请重试。')).then(result=>{
        if(result.ok && orderRevision===revision && (result.saved!==false || !state.config.taskOrder)) state.config.taskOrder=result.order;
        if(result.warning)toast(result.warning,'warning');
        render();
      }).catch(error=>{orderLoad=null;toast(error.message,'warning');throw error;});
    }
    return orderLoad;
  }
  function persistTaskOrder() {
    orderRevision++;
    if (!nativeMode)return;
    pendingOrder=D.clone(state.config.taskOrder||D.defaultOrders());
    clearTimeout(orderTimer);
    orderTimer=setTimeout(()=>flushTaskOrder().catch(error=>toast(error.message,'warning')),250);
  }
  function flushTaskOrder() {
    clearTimeout(orderTimer);
    if(pendingOrder){
      const order=pendingOrder;pendingOrder=null;
      orderSave=orderSave.catch(()=>{}).then(()=>withTimeout(N.call('save_task_order',order),3000,'保存顺序超时，本轮尚未开始。')).then(result=>{
        if(!result.ok)throw new Error('顺序未保存：'+result.error);
      });
    }
    return orderSave;
  }

  function queueGroup(group,source,selected,label) {
    const codes=D.orderedCatalog(state.config,group,source);
    return `<section class="queue-group"><h3>${label}</h3>${codes.map((code,index)=>{
      const task=source.find(row=>row.code===code),paused=code==='grocery',chosen=!paused&&selected.includes(code),locked=code==='arena_3v3';
      return `<div class="task-order-row queue-row ${chosen?'is-selected':''} ${state.taskSettings===code?'is-focused':''}" data-queue-group="${group}" data-queue-code="${code}"><span class="drag-grip" data-queue-drag draggable="${!locked}" aria-hidden="true">${locked?'▣':'⠿'}</span><label class="queue-select"><input type="checkbox" data-task-group="${group}" data-task-code="${code}" aria-label="选择${esc(task.label)}" ${paused?'disabled':''} ${chosen?'checked':''}><span class="queue-name"><strong>${esc(task.label)}</strong><small>${paused?'购买已暂缓':locked?'固定最后执行':chosen?'已加入本轮':'未选择'}</small></span></label><button class="queue-parameters" data-action="select-task" data-task-code="${code}" aria-label="设置${esc(task.label)}" aria-expanded="${state.taskSettings===code&&state.advancedOpen}">设置</button><div class="queue-arrows"><button class="text-button" data-action="task-up" data-task-group="${group}" data-task-code="${code}" aria-label="上移${esc(task.label)}" ${locked||index===0?'disabled':''}>↑</button><button class="text-button" data-action="task-down" data-task-group="${group}" data-task-code="${code}" aria-label="下移${esc(task.label)}" ${locked||index===codes.length-1||codes[index+1]==='arena_3v3'?'disabled':''}>↓</button></div></div>`;
    }).join('')}</section>`;
  }
  function renderQueue() {
    const c=state.config;
    let rows=c.section==='daily'&&c.dailyMode==='training_record'?'<section class="queue-group"><h3>修行录任务系列</h3><div class="queue-hint">先领取奖励，再连续完成当前账号的可靠目标；必要时核实新解锁目标，直到系列最终灰态。</div></section>':c.section==='daily'?queueGroup(c.scope==='current'?'current':'solo',catalogFor(c.scope==='current'?'current':'solo'),selectionFor(c.scope==='current'?'current':'solo'),c.scope==='current'?'当前账号任务':'日常任务'):c.section==='team'?c.teamMode==='auto'?queueGroup('team',D.TEAM_TASKS,c.teamTasks,'组队任务'):queueGroup('pipeline',D.PIPELINE_TASKS,c.pipelineTasks,'已有队伍任务')+queueGroup('solo',D.SOLO_TASKS,c.tasks,'解散后单人任务'):`<p class="queue-hint">在右侧选择本次活动与账号范围。</p>`;
    return `<aside class="task-queue"><div class="queue-heading"><span class="eyebrow">执行队列</span><h2>本轮任务</h2><p>点击名称勾选 · 点击设置查看参数</p></div><div class="queue-scroll">${rows}</div><div class="queue-footer">拖动左侧手柄或使用箭头排序<br>运行中固定顺序</div></aside>`;
  }
  function renderActivityRail() {
    const rail=document.getElementById('activity-rail');if(!rail)return;
    rail.hidden=state.page==='logs'||Boolean(state.tour);
    if(rail.hidden){rail.replaceChildren();delete rail.dataset.content;document.querySelector('.workspace-layout')?.classList.add('full-logs');return;}
    document.querySelector('.workspace-layout')?.classList.toggle('full-logs',state.page==='logs');
    const html=`<div class="activity-heading"><div><span class="eyebrow">运行现场</span><h2>实时日志</h2></div><button class="text-button" data-page="logs">展开</button></div><div class="activity-current"><strong>${esc(N?.info?.detail||state.snapshot?.detail||'就绪，等待开始')}</strong><span data-live-elapsed></span></div>${renderLogs()}`;
    if(rail.dataset.content===html)return;
    const old=rail.querySelector('.log-stream')?.parentElement;
    const scroll=old?.scrollTop||0;
    const active=document.activeElement;
    const focus=active?.id==='log-search'?{start:active.selectionStart,end:active.selectionEnd}:null;
    rail.innerHTML=html;rail.dataset.content=html;
    const stream=rail.querySelector('.log-stream')?.parentElement;
    if(stream)stream.scrollTop=state.autoScroll?stream.scrollHeight:scroll;
    if(focus){const input=rail.querySelector('#log-search');input?.focus({preventScroll:true});input?.setSelectionRange(focus.start,focus.end);}
  }

  function taskExecutionDetails(codes) {
    const rules = {
      mail: ['邮件附件', '领取当前角色可领取的邮件附件。', '附件领取结果核实后回村；普通背包已满时保留未领取邮件并提示处理。'],
      escort: ['押镖', '读取本日剩余次数，优先免费SS；免费SS不足时按免费刷新和铜币规则执行。', '逐趟核对币种、金额、免费额度、剩余次数及回村；仅在活动开放时开始。'],
      ranking: ['排位赛', '单人挑战第一位对手，沿用原场和次数记录。', '本号剩余挑战次数归零并回村后结束；未知原场先对账。'],
      survival: ['生存演习', '单人执行可用生存轮次，保留已完成战斗及领奖记录。', '战斗、领奖、剩余轮次和回村分别核实；已领取奖励不重复领取。'],
      quiz: ['忍者测验', '单人执行，题目全部选择第一项；今日次数已用尽时只核实。', '答题或既有完成状态与回村核实后结束。'],
      welfare: ['在线福利', '领取本号当前已解锁、可领取的在线奖励。', '所有当前可领取奖励核实后回村，尚未解锁的时段奖励不提前领取。'],
      grocery: ['杂货屋', '购买功能已暂缓，本版不执行购买。', '商品选择和原购买记录保留，待后续完善实时金额识别后恢复。'],
      group_instance: ['组队副本 · 单人执行', '先识别速刷预设，没有则用主力，都未识别则保持当前阵容；阵容检查后只进入一次副本，单人提示由本号确认。', '逐场核实奖励次数减少，剩余奖励次数归零并回村后结束。'],
      strong_approach: ['强者降临 · 单人执行', '默认识别并使用主力预设，未识别则保持当前阵容；只操作本号，自动战斗后开启并核实二倍速。', '奖励次数归零，或连续确认体力不足后，核实本号结束原因并回村。'],
      wanted: ['通缉任务 · 单人执行', '无需提前组队；确认单人执行后运行本号通缉自动链。', '通缉任务列表连续确认为空并回村后结束；未决自动链继续观察。'],
    };
    return codes.filter(code=>rules[code]).map(code=>{const [title,method,finish]=rules[code];return `<section class="parameter-box task-execution-details" data-task-details="${code}"><h3>${title}</h3><dl class="task-rules"><div><dt>执行方式</dt><dd>${method}</dd></div><div><dt>结束条件</dt><dd>${finish}</dd></div></dl></section>`;}).join('');
  }

  function renderSoloParameters() {
    const config = state.config;
    const selectedCodes=D.selectedSoloTasks(config);
    const codes=D.CURRENT_TASKS.some(row=>row.code===state.taskSettings)?[state.taskSettings]:selectedCodes;
    const currentBond=config.scope==='current'&&codes.includes('bond')?currentBondControls():'';
    const arena = codes.includes('arena') ? `<div class="parameter-box"><h3>竞技场结束条件</h3><p>按实际战令或所选胜场目标结束，奖励条件单独显示。</p>${arenaControls()}</div>` : '';
    const escort = codes.includes('escort') ? `<div class="parameter-box"><h3>押镖续做</h3><p>默认按完整三次执行；已交付过的次数可选择只做剩余次数，并单独核实本次结果。</p><div class="form-row"><label class="field"><span>执行范围</span><select data-config="escortRemaining"><option value="0" ${config.escortRemaining === 0 ? 'selected' : ''}>完整三次（默认）</option><option value="2" ${config.escortRemaining === 2 ? 'selected' : ''}>仅剩余两次</option><option value="1" ${config.escortRemaining === 1 ? 'selected' : ''}>仅剩余一次</option></select></label>${config.escortRemaining ? `<label class="switch-label"><input type="checkbox" data-config="escortPriorSettled" ${config.escortPriorSettled ? 'checked' : ''}>确认此前已消耗的押镖均已交付结束</label>` : ''}</div><p>续做仅限当前账号、只勾选押镖，并关闭最后托管；次数和免费额度仍需现场核实。</p></div>` : '';
    const fixed = [
      codes.includes('daruma') ? '达摩：连续确认奖励次数为 0 并回村后才记完成。' : '',
      codes.includes('weekend') ? '周末任务：拼图三个难度与达摩；未开放时跳过且不计完成。' : '',
      codes.includes('treasure') ? '挖宝：每号固定 5 分钟，只使用已有地图，到时暂停并回村。' : '',
    ].filter(Boolean);
    return `<details class="advanced-panel" data-advanced ${state.advancedOpen ? 'open' : ''}><summary><span>任务参数与收尾 <span class="advanced-summary">· ${codes.length===1?esc(D.taskLabel(codes[0])):`${codes.length}项任务`} · ${config.finalManaged ? '最后托管开启' : '最后托管关闭'}</span></span></summary><div class="advanced-content">
      ${taskExecutionDetails(codes)}${arena}${codes.includes('grocery')?groceryControls():''}${escort}${currentBond}${fixed.length ? `<div class="fixed-rule">${icon('info')}<div>${fixed.map((line) => `<div>${line}</div>`).join('')}</div></div>` : ''}
      ${commonControls()}
    </div></details>`;
  }

  function arenaControls() {
    const config = state.config;
    return `<div class="form-row"><div class="radio-group">
      ${radioOption('arenaTargetMode', 'tickets', '战令归零（默认）')}
      ${radioOption('arenaTargetMode', 'wins', '指定胜场（辅助）')}
    </div><label class="field field-inline"><span>目标胜场</span><input type="number" min="1" max="10" value="${config.arenaWins}" data-config="arenaWins" ${config.arenaTargetMode !== 'wins' ? 'disabled' : ''}></label></div>${arenaSkillControls()}`;
  }

  function arenaSkillControls() {
    return `<label class="switch-label"><input type="checkbox" data-config="arenaSecondSkill" ${state.config.arenaSecondSkill?'checked':''}>竞技场开场使用第二技能（默认关闭）</label><p class="muted">自动战斗照常开启；第二技能开启后每场只发送一次。</p>`;
  }

  const groceryItems=[['practice_potion','修行药水'],['secret_pages','秘卷残页'],['myoboku_harvest','妙木山的收获'],['myoboku_training','妙木山的训练'],['myoboku_orb','妙木山的灵球'],['cultivation_ticket','修炼券'],['mantra_elixir','真言合剂']];
  function groceryControls() {
    return `<section class="parameter-box grocery-settings"><h3>杂货屋 · 已暂缓</h3><p>购买功能暂不启用，商品金额以游戏实时显示为准。</p><div class="task-grid grocery-item-grid">${groceryItems.map(([code,label])=>`<label class="task-card"><input type="checkbox" data-grocery-item="${code}" disabled ${(state.config.groceryItems||[]).includes(code)?'checked':''}><span><strong>${label}</strong><small>原选择保留 · 本版不购买</small></span></label>`).join('')}</div><p>原交易凭据保留，不自动重试未决购买。</p></section>`;
  }

  const tourSteps=[['accounts','账号范围'],['tasks','任务选择与顺序'],['parameters','任务参数'],['team','组队与独立任务'],['grocery','杂货屋购买设置'],['finish','收尾、保存与日志']];
  function openOnboarding() {
    if(nativeMode&&N.info?.busy){toast('当前任务结束后可查看新人引导。','warning');return;}
    if(state.tour?.busy)return;
    closeDialog();state.page='plan';state.advancedOpen=true;
    state.tour={index:state.onboarding?.required?Math.min(5,state.onboarding.steps?.length||0):0};
    render();main.scrollTop=0;
  }
  function renderTour() {
    const step=state.tour.index,original=state.config;
    let body='';
    try {
      // Route the actual settings components without changing saved mode/scope.
      state.config={...original,section:'daily',dailyMode:'manual'};
      if(step===0)body=`<h3>先确认操作哪些账号</h3><div class="choice-grid">${scopeChoice('current','当前账号','只操作当前游戏窗口','user')}${scopeChoice('multi','多个账号','从标签1开始，最多六个标签','users')}</div>${state.config.scope==='multi'?renderAccountCount():'<p>当前账号模式只操作当前标签。选择多个账号后可设置标签数量。</p>'}<p>标签按游戏浏览器从左到右的位置识别。正式开始时核实账号；检查并回村本身不启动日常任务。</p>`;
      if(step===1)body=`<p>勾选加入本轮，上下箭头或拖拽调整顺序。组队副本、强者位于竞技场前，默认不选。</p>${queueGroup('current',D.CURRENT_TASKS,selectionFor('current'),'任务队列')}${taskTools('current')}`;
      if(step===2){
        state.advancedOpen=true;
        body=`<p>点击任务名称查看参数；查看不代表勾选执行。</p><div class="tour-task-tabs">${D.SOLO_TASKS.filter(t=>t.code!=='grocery').map(t=>`<button class="button button-secondary button-small" data-action="select-task" data-task-code="${t.code}">${t.label}</button>`).join('')}</div>${renderSoloParameters()}`;
      }
      if(step===3){state.config={...original,section:'team'};body=renderTeam();state.config={...original,section:'independent'};body+=renderIndependent();}
      if(step===4)body=groceryControls();
      if(step===5)body=commonControls()+`<h3>保存后再开始</h3><p>请核对阵容和最后托管开关。点击下方“保存并完成引导”只保存设置；任务由你随后点击开始。</p><p>运行中可点击停止或按 F12，等待操作结束后关闭。运行时间会在任务结束后停止增加。</p><h3>桌面中文日志</h3><p>完整记录自动保存到桌面“火影OL日志”，按日期和运行批次整理。运行日志页可直接打开目录或导出筛选结果。</p>`;
    } finally {state.config=original;}
    return `<section class="card onboarding-card"><div class="tour-heading"><span class="eyebrow">首次设置导览 · ${step+1} / 6</span><h2>${tourSteps[step][1]}</h2><p>可逐组查看，也可跳过后直接使用。跳过不覆盖已有配置，不会自动开始任务；可在使用说明中重新查看。</p><div class="tour-progress">${tourSteps.map((s,i)=>`<span class="${i<=step?'is-viewed':''}">${i+1}. ${s[1]}</span>`).join('')}</div></div><div class="card-body">${body}</div><div class="tour-actions"><button class="button button-secondary" data-action="tour-skip" ${state.tour.busy?'disabled':''}>一键跳过全部引导</button><button class="button button-secondary" data-action="tour-back" ${step===0||state.tour.busy?'disabled':''}>上一组</button><button class="button button-primary" data-action="tour-next" ${state.tour.busy?'disabled':''}>${state.tour.busy?'正在保存…':step===5?'保存并完成引导':'已查看，下一组'}</button></div></section>`;
  }
  const tourCall=(method,...args)=>withTimeout(N.call(method,...args),5000,'引导操作响应超时，结果尚未确认。可重试或点击“跳过引导”。');
  async function skipTour() {
    if(!state.tour||state.tour.busy)return;
    const tour=state.tour; tour.busy=true;render();
    try {
      const row=nativeMode?await tourCall('skip_onboarding'):{ok:true,required:false,steps:state.onboarding?.steps||[]};
      if(!row?.ok||row.required!==false)throw new Error(row?.error||'跳过引导未确认，请重试。');
      if(state.tour!==tour)return;
      state.onboarding=row;state.tour=null;
      toast('已跳过引导，未覆盖已有配置。请核对执行清单后手动开始。');
    }catch(error){if(state.tour===tour){tour.busy=false;toast(error.message,'warning');}}
    render();main.scrollTop=0;
  }
  async function advanceTour() {
    if(!state.tour||state.tour.busy)return;
    const step=state.tour.index;state.tour.busy=true;render();
    try {
      if(nativeMode){const row=await tourCall('acknowledge_onboarding',tourSteps[step][0]);if(!row?.ok)throw new Error(row?.error||'引导进度未保存');state.onboarding=row;}
      if(step===5){
        if(nativeMode){const row=await tourCall('complete_onboarding',D.clone(state.config));if(!row?.ok)throw new Error(row?.error||'设置未保存');state.onboarding=row;}
        else {localStorage.setItem('hyol-desktop-settings',JSON.stringify(state.config));state.onboarding={required:false,steps:tourSteps.map(s=>s[0])};}
        state.tour=null;toast('设置已保存，可以核对执行清单后开始。');
      } else state.tour={index:step+1};
    }catch(error){toast(error.message,'warning');if(state.tour)state.tour.busy=false;}
    render();main.scrollTop=0;
  }

  function currentBondControls() {
    const config=state.config;
    const field=config.bondTargetMode==='kills'?['bondKillTarget','领取目标人数档',0,999]
      :config.bondTargetMode==='score'?['bondScoreTarget','结束积分上限',0,99999]:['bondCount','本次场数',1,99];
    return `<div class="parameter-box"><h3>本号羁绊结束条件</h3><p>使用已有队伍，只操作当前账号；完成后保留队伍及准备页。</p><div class="form-row"><label class="field"><span>结束条件</span><select data-config="bondTargetMode">${[['battles','固定场数'],['kills','击杀宝箱档位达标'],['score','积分降到指定值']].map(([value,label])=>`<option value="${value}" ${config.bondTargetMode===value?'selected':''}>${label}</option>`).join('')}</select></label><label class="field"><span>${field[1]}</span><input type="number" min="${field[2]}" max="${field[3]}" value="${config[field[0]]}" data-config="${field[0]}"></label></div></div>`;
  }

  function radioOption(key, value, label) {
    return `<label class="radio-option"><input type="radio" name="${key}" value="${value}" data-config="${key}" ${state.config[key] === value ? 'checked' : ''}><span>${label}</span></label>`;
  }

  function commonControls() {
    const config = state.config;
    const activityPreset=config.section==='daily'&&config.scope==='current'&&config.dailyMode!=='training_record'&&config.currentTeamTasks.some(code=>['group_instance','strong_approach'].includes(code));
    const keepTeam=config.section==='daily'&&config.scope==='current'&&config.dailyMode!=='training_record'&&config.currentTeamTasks.some(code=>['bond','arena_3v3'].includes(code));
    const locked=config.section==='team'&&config.teamMode==='pipeline'&&config.pipelineTasks.includes('arena_3v3');
    const training=config.dailyMode==='training_record'&&config.section==='daily'||config.section==='independent'&&config.independent==='training_record'||config.section==='daily'&&config.tasks.includes('training_record');
    const trainingControls=training?`<div class="parameter-box"><h3>修行录竞技场结束条件</h3><label class="field"><select data-config="trainingArenaStopPolicy">${[['auto','按执行日自动选择（周日完成最终目标，其余日战令用完）'],['final_goal','完成任务系列最终目标'],['tickets','本号战令用完']].map(([value,label])=>`<option value="${value}" ${config.trainingArenaStopPolicy===value?'selected':''}>${label}</option>`).join('')}</select></label><p>手动选择优先；其余修行录目标仍执行到最终灰态。</p></div>`:'';
    return `${trainingControls}${training?arenaSkillControls():''}<div class="parameter-box"><h3>阵容与最后收尾</h3><p>完成判定仍须包含任务、领奖与回村证据；托管不替代这些判定。</p>${activityPreset?'<p>组队副本依次选择速刷、主力；强者优先主力，未识别则保持当前。下方阵容选项用于其他任务。</p>':''}<div class="form-row"><label class="field"><span>${activityPreset?'其他任务阵容':'阵容'}</span><select data-config="formation" ${locked?'disabled':''}><option value="keep" ${config.formation === 'keep' ? 'selected' : ''}>保持不变（默认）</option><option value="main" ${config.formation === 'main' ? 'selected' : ''}>主力阵容</option><option value="fast" ${config.formation === 'fast' ? 'selected' : ''}>速刷阵容</option></select></label><label class="switch-label"><input type="checkbox" data-config="finalManaged" ${config.finalManaged&&!keepTeam ? 'checked' : ''} ${keepTeam?'disabled':''}>${keepTeam?'当前账号组队活动结束保留队伍':'最后进入托管（默认开启）'}</label></div></div>`;
  }

  function renderTeam() {
    const config = state.config;
    return `<section class="card">${cardHeader('01', '组队方式', '选择已有队伍或自动建队，再安排活动与收尾。')}
      <div class="card-body"><div class="team-mode-tabs"><button class="team-mode-tab ${config.teamMode === 'auto' ? 'is-active' : ''}" data-team-mode="auto">自动建队</button><button class="team-mode-tab ${config.teamMode === 'pipeline' ? 'is-active' : ''}" data-team-mode="pipeline">已有队伍 → 单人</button></div>
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
    return `<div class="phase-flow"><div class="phase-card"><strong>阶段 1 · 已核实队伍</strong><span>${config.pipelineTasks.includes('arena_3v3')?'保留已有三人队；先做其他活动，3v3固定最后':'已有队伍依次执行；二人队可继续羁绊'}</span></div><div class="phase-arrow">${icon('arrow')}</div><div class="phase-card"><strong>阶段 2 · 解散后单人</strong><span>${config.pipelineTasks.includes('arena_3v3')?'3v3退出后核实解散，再执行所选日常':'组队任务结束并确认解散后，再执行所选日常'}</span></div></div>
      ${config.pipelineTasks.includes('arena_3v3')?renderTrioFields():''}<div class="parameter-box"><div class="form-row"><label class="switch-label"><input type="checkbox" data-config="pipelineAuto" ${config.pipelineAuto ? 'checked' : ''}>自动发现当前队伍</label><button class="button button-secondary button-small" data-action="detect-leaders">${icon('eye')}查看当前队长</button></div>
      <div class="form-row" style="margin-top:13px">${[0, 1, 2].map((index) => `<label class="field"><span>第 ${index + 1} 队队长${index > 0 ? '（可不选）' : ''}</span><select data-pipeline-leader="${index}" ${config.pipelineAuto ? 'disabled' : ''}>${index > 0 ? `<option value="" ${!config.pipelineLeaders[index] ? 'selected' : ''}>不选</option>` : ''}${[1, 2, 3, 4, 5, 6].map((slot) => `<option value="${slot}" ${config.pipelineLeaders[index] === slot ? 'selected' : ''}>标签 ${slot}</option>`).join('')}</select></label>`).join('')}</div>
      <div class="inline-note">${icon('info')}<span>${state.detectedLeaders || '只读识别最多六个标签；队长推进任务，战斗逐号确认全队自动；羁绊不检查倍速。'}</span></div><label class="switch-label" style="margin-top:12px"><input type="checkbox" data-config="preformedDuos" ${config.preformedDuos ? 'checked' : ''}>已组好二人队，保留当前队伍直接执行</label>
      ${config.preformedDuos ? `<div class="form-row" style="margin-top:12px">${(config.pipelineAuto ? [1,3,5] : config.pipelineLeaders.filter(Boolean)).map(leader => `<label class="field"><span>队长标签 ${leader} 的队员</span><select data-preformed-member="${leader}"><option value="">${'请选择'}</option>${[1,2,3,4,5,6].filter(slot=>slot!==leader).map(slot=>`<option value="${slot}" ${config.preformedPairs.find(pair=>pair[0]===leader)?.[1]===slot ? 'selected' : ''}>标签 ${slot}</option>`).join('')}</select></label>`).join('')}</div>` : ''}</div>
      <div class="divider"></div><div class="card-header" style="padding:0"><div><h2>多人阶段任务</h2><p>副本、强者优先；通缉错峰时每120秒检查，其他队完成即恢复。</p></div><div class="card-tools">${taskTools('pipeline')}</div></div>
      <div style="margin:14px 0 20px">${renderTaskGrid(D.PIPELINE_TASKS, config.pipelineTasks, 'pipeline')}</div>
      ${config.pipelineTasks.includes('bond') ? renderBondGoals() : ''}
      <div class="card-header" style="padding:0"><div><h2>解散后执行的单人任务</h2><p>可全部取消，只运行多人任务及所选羁绊；最后托管可单独关闭。</p></div><div class="card-tools">${taskTools('solo')}</div></div>
      <div style="margin-top:14px">${renderTaskGrid(D.SOLO_TASKS, config.tasks, 'solo')}</div>
      ${renderSoloParameters()}`;
  }

  function renderTrioFields() {
    const trio=state.config.preformedTrio||[1,2,3];
    return `<div class="parameter-box"><h3>已有三人队 · 限时竞技场3v3</h3><p>先做完其他组队任务，再打3v3；三人奖励全部领取后退出，确认解散再做单人。已在3v3准备页会保留当前页面。</p><div class="trio-fields">${['队长标签','队员一标签','队员二标签'].map((label,i)=>`<label class="field"><span>${label}</span><select data-trio-index="${i}">${[1,2,3,4,5,6].map(slot=>`<option value="${slot}" ${trio[i]===slot?'selected':''}>标签 ${slot}</option>`).join('')}</select></label>`).join('')}</div><div class="inline-note">目标：每人六项任务奖励＋三个进度宝箱；保持当前阵容。</div></div>`;
  }

  function renderBondGoals() {
    const config = state.config;
    const modes = [['battles','固定场数'],['kills','击杀宝箱档位达标'],['score','积分降到指定值']];
    const options = (selected) => modes.map(([value,label]) => `<option value="${value}" ${selected === value ? 'selected' : ''}>${label}</option>`).join('');
    const field = config.bondTargetMode === 'kills' ? ['bondKillTarget','领取至此人数档（打满为160）',0,999]
      : config.bondTargetMode === 'score' ? ['bondScoreTarget','积分降至此值及以下',0,99999] : ['bondCount','场数',1,99];
    const leaders = config.preformedPairs.length ? config.preformedPairs.map(pair=>pair[0]) : config.preformedDuos ? [1,3,5] : (config.pipelineAuto ? [1,2,3,4,5,6] : config.pipelineLeaders.filter(Boolean));
    return `<div class="parameter-box"><h3>羁绊结束条件</h3><p>仅检查队长并连续匹配，队长达标后集中领取击杀宝箱；最终含160人档领取完成。可给弱队单独设置掉分目标。</p>
      <div class="form-row"><label class="field"><span>默认结束条件</span><select data-config="bondTargetMode">${options(config.bondTargetMode)}</select></label>
      <label class="field"><span>${field[1]}</span><input type="number" min="${field[2]}" max="${field[3]}" value="${config[field[0]]}" data-config="${field[0]}"></label></div>
      ${leaders.map((leader) => { const row = config.bondTeamGoals.find(item => item[0] === leader); const member = config.preformedPairs.find(pair=>pair[0]===leader)?.[1] || leader+1; return `<div class="form-row" style="margin-top:12px"><label class="field"><span>${config.preformedDuos ? `标签 ${leader}+${member}` : `队长标签 ${leader}`} · 单队目标</span><select data-bond-team-mode="${leader}"><option value="inherit" ${!row ? 'selected' : ''}>沿用默认</option>${options(row?.[1])}</select></label><label class="field"><span>${row?.[1] === 'score' ? '积分降至此值及以下' : row?.[1] === 'kills' ? '领取档位（0 = 160人档）' : '目标数值'}</span><input type="number" min="${row?.[1] === 'battles' ? 1 : 0}" max="${({battles:99,kills:999,score:99999})[row?.[1]] || 99999}" value="${row?.[2] ?? ''}" data-bond-team-target="${leader}" ${row ? '' : 'disabled'}></label></div>`; }).join('')}</div>`;
  }

  function renderIndependent() {
    const config = state.config;
    return `<section class="card">${cardHeader('01', '选择独立任务', '仅支持当前一个游戏窗口；每次只运行一种任务，不切换其他账号。')}
      <div class="card-body"><div class="independent-grid">${D.INDEPENDENT_TASKS.map((task) => `<button class="independent-card ${config.independent === task.code ? 'is-active' : ''}" data-independent="${task.code}" aria-pressed="${config.independent === task.code}">${icon(task.icon)}<strong>${task.label}</strong><small>${task.description}</small></button>`).join('')}</div></div></section>
      <section class="card">${cardHeader('02', `${D.taskLabel(config.independent)}设置`, '只显示当前任务实际支持的参数；按当前任务设置执行目标与收尾。')}<div class="card-body">${renderIndependentSettings()}</div></section>`;
  }

  function renderIndependentSettings() {
    const config = state.config;
    if (['puzzle', 'training_record'].includes(config.independent)) {
      const message = config.independent === 'puzzle'
        ? '使用原拼图程序，按账号完成简单、普通和困难。活动日05:00刷新；已领取项只复核，未知开始或领奖保留断点。'
        : '先领取已达成奖励，再连续完成本号可靠目标；仅在必要解锁边界复核，直到任务系列最终灰态。领奖结果不明时暂停该账号排任务。';
      return `<div class="fixed-rule" style="margin-top:10px">${icon(config.independent === 'puzzle' ? 'weekend' : 'logs')}<div>仅当前窗口。${message}</div></div><div style="margin-top:10px">${commonControls()}</div>`;
    }
    if (config.independent === 'arena') {
      return `<div class="parameter-box"><h3>结束条件与阵容</h3><p>按实际战令或所选胜场目标结束，奖励条件单独显示。</p>${arenaControls()}</div><div style="margin-top:10px">${commonControls()}</div>`;
    }
    if (config.independent === 'bond') {
      return `<div class="parameter-box"><h3>羁绊目标</h3><p>请先手动进入羁绊准备页，再启动脚本。</p><div class="form-row"><label class="field"><span>结束条件</span><select data-config="bondStopMode">${[['endless','一直打'],['battles','打5次'],['wins','5场胜利']].map(([value,label])=>`<option value="${value}" ${config.bondStopMode===value?'selected':''}>${label}</option>`).join('')}</select></label><label class="switch-label"><input type="checkbox" data-config="newBondRound" ${config.newBondRound ? 'checked' : ''}>开始新一轮羁绊</label></div></div>
      <div class="fixed-rule" style="margin-top:10px">${icon('shield')}<div>仅当前一个窗口：匹配 → 检查自动 → 等待结束 → 下一场。打5次按胜负都计数；5场胜利只累计确认的胜利。一直打需手动停止或按F12。达到目标后保留队伍和准备页；不自动进入活动，不领取宝箱，不回村或托管。中断后继续原轮次，未决场次不重复匹配。</div></div>`;
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
    const baseFormation = { keep: '保持不变', main: '主力阵容', fast: '速刷阵容' }[config.formation];
    const specialFormation=plan.request.mode==='solo'&&plan.request.tasks?.some(code=>['group_instance','strong_approach'].includes(code));
    const formation=specialFormation?'按活动优先级；其他'+baseFormation:baseFormation;
    const groceryCount = plan.request.tasks?.includes('grocery') ? (plan.request.grocery_items||[]).length : 0;
    let index = 0;
    const phases = plan.phases.map((phase) => `<div class="summary-phase"><div class="summary-phase-label">${esc(phase.label)}</div>${phase.tasks.length ? phase.tasks.map((code) => {
      index += 1;
      return `<div class="summary-task"><span class="summary-index">${index}</span><div><strong>${D.taskLabel(code)}</strong><small>${esc(D.detailFor(code, config))}</small></div></div>`;
    }).join('') : '<div class="summary-empty">尚未选择任务</div>'}</div>`).join('');
    return `<aside class="card summary-card"><div class="summary-head"><h2>执行清单</h2>${statusBadge(status === 'not_open' ? 'skipped' : status === 'complete' ? 'done' : status === 'running' ? 'running' : status === 'pending' ? 'pending' : 'new', runMeta(status).label)}</div><div class="summary-content"><div class="summary-scope"><strong>${esc(plan.title)}</strong><span>开始前只创建演示请求；正式版将在运行时再核实账号与画面。</span></div><div class="summary-phases">${phases}</div><div class="summary-settings"><div class="summary-setting"><span>阵容</span><strong>${config.section === 'independent' && config.independent === 'bond' ? '不适用' : formation}</strong></div><div class="summary-setting"><span>最后托管</span><strong>${config.section === 'independent' && config.independent === 'bond' ? '不执行，保留羁绊准备页' : plan.request.final_managed ? '开启（默认）' : '关闭'}</strong></div><div class="summary-setting"><span>杂货屋</span><strong>购买已暂缓</strong></div></div><div class="safety-note">${icon('shield')}<span>${error ? `配置待修正：${esc(error)}` : '完成必须有任务、领奖与回村证据。F12、识别阈值、INI 与断点格式仍由原运行层负责。'}</span></div><button class="button button-secondary button-small" style="width:100%;margin-top:13px" data-action="show-request">${icon('code')}查看演示 RunRequest</button></div></aside>`;
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
      <div class="table-wrap"><table class="progress-table"><thead><tr><th>账号 / 标签</th><th>账号状态 / 当前任务</th>${snapshot.taskCodes.map((code) => `<th>${D.taskLabel(code)}</th>`).join('')}</tr></thead><tbody>${renderAccountRows()}</tbody></table></div><div class="progress-footer"><span>最近核实：${esc(snapshot.updatedAt || '暂无记录')}${snapshot.warning ? ' · 当前为上次有效快照' : ''}</span><span class="current-only">${icon('shield')}只读浏览 · 仅本轮 · 查看不点击游戏</span></div></section>${renderTrainingGoals()}`;
  }

  function trainingPlanText(rec) {
    const plan = rec.daily_plan;
    if (rec.rewards_complete === false || plan?.rewards_complete === false) return '先领取并核实全部可领奖励，随后计算本号日常';
    if (!plan) return '等待领奖后重新识别本号日常';
    const statuses = { ready: '待执行', running: '执行中', waiting: '等待结算，其他账号继续', satisfied: '本次目标已满足', operation_complete: '完整步骤已完成，数量待复扫', exhausted: '可用次数已用完', checked: '本次已处理', pending: '待核实', deferred: '保留待续', not_open: '今日未开放' };
    return plan.tasks.length ? plan.tasks.map(item => {
      const job = rec.active_job?.task === item.task ? rec.active_job : item;
      const units = {count:'次', wins:'胜', points:'积分', layers:'层', claims:'项'};
      const needed = Object.entries(job.deficits || {}).map(([unit,n]) => {
        const verified = unit === job.unit && Number.isInteger(job.settled) ? job.settled : 0;
        return `${Math.max(0,n-verified)}${units[unit] || ''}`;
      }).join('、');
      const quotaLabel = item.task === 'arena' ? '剩余战令' : job.quota_unit === 'current_grade_challenges' ? `${job.grade || '当前难度'}剩余挑战` : '剩余可用';
      const quota = job.available == null ? '可用数量待核实' : `${quotaLabel} ${job.available_lower_bound ? '至少' : ''}${job.available}${item.task === 'arena' ? '个' : '次'}`;
      const settled = job.settled == null ? '' : ` · 本次已核实 ${job.settled}${units[job.unit] || '次'}`;
      const policy = job.date_policy?.label ? ` · ${job.date_policy.label}` : '';
      return `${D.taskLabel(item.task)}${needed ? ` · 还需 ${needed}` : ''} · ${quota}${settled}${policy}（${statuses[job.status] || '待核实'}）${job.stop_reason ? ` · ${readable(job.stop_reason)}` : ''}`;
    }).join('；') : '当前无需新增可执行日常';
  }

  function renderTrainingGoals() {
    if (!state.snapshot?.taskCodes.includes('training_record')) return '';
    const rows = state.snapshot.accounts.flatMap(account => {
      const rec = account.tasks.training_record || {};
      const goals = rec.latest?.rows || rec.after?.rows || rec.before?.rows || [];
      const planRow = `<tr data-training-plan="${esc(account.slot)}"><td>${esc(account.label)}</td><td colspan="3"><strong>本号日常：</strong>${esc(trainingPlanText(rec))}</td></tr>`;
      if (!goals.length) return [planRow, `<tr><td>${esc(account.label)}</td><td colspan="3">${esc(readable(rec.last_error || '等待扫描该账号的修行录'))}</td></tr>`];
      return [planRow, ...goals.map(goal => `<tr><td>${esc(account.label)}</td><td>${esc(goal.description)}</td><td>${goal.progress_known ? `${esc(goal.current)} / ${esc(goal.target)}` : '计数待核实'}</td><td>${goal.series_state==='terminal_claimed'&&goal.terminal_gray_verified===true&&goal.terminal_samples?.length>=2 ? '系列已完成' : goal.tier_claimed||goal.completed ? '当前档已领取，后续待核实' : goal.claimable ? esc(readable(goal.reason || '目标已达成，奖励待领取')) : esc(readable(rec.errors?.[goal.task] || goal.reason || (goal.task ? '待执行对应任务' : '待处理')))}</td></tr>`)];
    });
    return `<section class="card training-goals"><div class="card-header"><div><h2>修行录目标清单</h2><p>先领完奖励，再连续执行可靠目标；只有最终灰态双帧核实才标记系列完成。</p></div></div><div class="table-wrap"><table class="progress-table"><thead><tr><th>账号</th><th>目标</th><th>进度</th><th>处理结果</th></tr></thead><tbody>${rows.join('')}</tbody></table></div></section>`;
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
        const label = rec.outcome_label || (snapshot.status === 'not_open' && rec.status === 'skipped' ? '活动未开放' : '');
        const hint = rec.status === 'done' ? '查看核实记录' : rec.status === 'pending' ? '查看待处理原因' : rec.status === 'offline' ? '本轮已跳过' : rec.status === 'running' ? '结果核实中' : rec.status === 'skipped' ? '未执行' : '等待执行';
        return `<td><button class="task-cell" data-action="open-detail" data-account="${account.slot}" data-task="${code}" aria-label="${esc(`${account.label} ${D.taskLabel(code)} ${D.STATUS[rec.status]?.label || rec.status}`)}">${statusBadge(rec.status, label)}<small>${esc(rec.daily_observation?.label||hint)}</small></button></td>`;
      }).join('')}</tr>`;
    }).join('');
  }

  function allLogs() {
    return (state.snapshot?.logs || N?.info?.logs || []).filter(row=>!nativeMode || (row.id||0)>state.logClearedId);
  }
  function readable(value,fallback='状态待核实，请查看诊断记录。') {
    if(!value)return '';
    const text=String(value).replace(/(?:SafetyError|PendingError|RuntimeError|ValueError|TypeError):\s*/g,'');
    const labels={ready:'检查通过',pending:'待核实',running:'执行中',done:'已完成',new:'未执行',offline:'已掉线',stopped:'已停止',complete:'已完成',unknown:'尚未确认',idle:'就绪'};
    if(labels[text])return labels[text];
    return /[\u3400-\u9fff]/.test(text)&&!/[A-Za-z_]{3,}/.test(text.replace(/OpenCV|YOLO|QQ|F12|VIP/g,''))?text:fallback;
  }
  function logLine(entry) {
    const level={info:'信息',success:'已确认',warning:'注意',error:'异常',debug:'诊断'}[entry.level]||'信息';
    const repeated=entry.repeat>1?`（累计${entry.repeat}次，首次${entry.first_time||entry.time}）`:'';
    return `${entry.time} ${level} ${entry.account} ${entry.task_label||''} ${entry.message}${repeated}${entry.suggestion?' '+entry.suggestion:''}${state.logLevel==='debug'?'\n原始诊断：'+(entry.diagnostic||entry.event):''}`;
  }
  function renderLogs() {
    const logs = allLogs();
    const accounts = [...new Set(logs.map((entry) => entry.account))];
    return `<section class="card logs-card"><div class="logs-toolbar"><input id="log-search" type="search" placeholder="搜索事件、账号或日志内容" value="${esc(state.logSearch)}" aria-label="搜索日志"><select id="log-level" aria-label="日志级别"><option value="all" ${state.logLevel === 'all' ? 'selected' : ''}>全部级别</option><option value="debug" ${state.logLevel === 'debug'?'selected':''}>调试原始事件</option><option value="info" ${state.logLevel === 'info' ? 'selected' : ''}>信息</option><option value="success" ${state.logLevel === 'success' ? 'selected' : ''}>已确认</option><option value="warning" ${state.logLevel === 'warning' ? 'selected' : ''}>注意</option><option value="error" ${state.logLevel === 'error' ? 'selected' : ''}>异常</option></select><select id="log-account" aria-label="日志账号"><option value="all">全部账号</option>${accounts.map((account) => `<option value="${esc(account)}" ${state.logAccount === account ? 'selected' : ''}>${esc(account)}</option>`).join('')}</select><select id="log-task" aria-label="日志任务"><option value="all">全部任务</option>${[...new Set(logs.map(e=>e.task).filter(Boolean))].map(code=>`<option value="${esc(code)}" ${state.logTask===code?'selected':''}>${esc(D.taskLabel(code))}</option>`).join('')}</select><span class="toolbar-spacer"></span><button class="text-button" data-action="copy-logs">复制</button><button class="button button-secondary button-small" data-action="export-logs">${icon('download')}导出当前日志</button><button class="button button-ghost button-small" data-action="clear-logs">${icon('trash')}清空显示</button></div><div class="logs-subbar"><span id="log-count">${filteredLogs().length} / ${logs.length} 条 · 最近运行记录</span><div><label class="switch-label"><input id="auto-scroll" type="checkbox" ${state.autoScroll ? 'checked' : ''}>新日志自动到底部</label><button class="text-button" data-action="open-log-directory">${icon('folder')}打开日志目录</button></div></div><div class="table-wrap"><div class="log-stream">${renderLogRows()}</div></div></section>`;
  }

  function filteredLogs() {
    const query = state.logSearch.trim().toLowerCase();
    return allLogs().filter((entry) => {
      if(state.logLevel==='all' && entry.level==='debug')return false;
      if(state.logTask!=='all' && entry.task!==state.logTask)return false;
      if (state.logLevel !== 'all' && entry.level !== state.logLevel) return false;
      if (state.logAccount !== 'all' && entry.account !== state.logAccount) return false;
      if (query && !`${entry.account} ${entry.event} ${entry.message} ${entry.suggestion||''} ${entry.task_label||''}`.toLowerCase().includes(query)) return false;
      return true;
    });
  }

  function renderLogRows() {
    const logs = filteredLogs();
    if (!logs.length) return `<div class="log-empty">${state.snapshot ? '没有符合当前条件的日志。' : '尚未开始本轮，暂无运行日志。'}<br><button class="text-button" data-action="clear-log-filters">清除筛选</button></div>`;
    const levels = { info: '信息', success: '已确认', warning: '注意', error: '异常', debug:'诊断' };
    return logs.map((entry) => `<div class="log-row"><time class="log-time">${esc(entry.time)}</time><span class="log-level ${entry.level}">${levels[entry.level] || '信息'}</span><span class="log-account">${esc(entry.account)}</span><div class="log-message"><p>${esc(entry.message)}${entry.repeat>1?` <small>（累计 ${entry.repeat} 次，首次 ${esc(entry.first_time||entry.time)}）</small>`:''}</p>${entry.suggestion?`<p class="log-suggestion">${esc(entry.suggestion)}</p>`:''}${entry.diagnostic?`<details class="log-diagnostic"><summary>原始诊断</summary><pre>${esc(entry.event)}\n${esc(entry.diagnostic)}</pre></details>`:''}</div></div>`).join('');
  }

  function applyNativeLabels() {
    if (!nativeMode) return;
    const scope = main.querySelector('.summary-scope span');
    if (scope) scope.textContent = '开始时核实当前游戏窗口与账号，只执行本轮选择。';
    const requestButton = main.querySelector('[data-action="show-request"]');
    if (requestButton) requestButton.innerHTML = `${icon('code')}查看任务参数`;
    const exportButton = main.querySelector('[data-action="export-logs"]');
    if (exportButton) exportButton.innerHTML = `${icon('download')}导出当前日志`;
    const count = document.querySelector('#log-count');
    if (count) count.textContent = `${filteredLogs().length} / ${allLogs().length} 条 · 最近日志，完整记录见日志目录`;
    main.querySelectorAll('.card-header p').forEach(order => {
      if (order.textContent.includes('SOLO_OPTIONS')) order.textContent = '按你设置的任务顺序执行。';
    });
    const safety = main.querySelector('.safety-note span');
    if (safety && !safety.textContent.startsWith('配置待修正')) safety.textContent = '完成须核实任务、领奖与回村；杂货屋只购买所选月之印商品。运行中可停止本轮或按F12。';
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
      const request = D.requestFor(state.config);
      const slots = request.mode === 'team_then_multi' ? request.preformed_trio?.length?request.preformed_trio:[1,2,3,4,5,6]
        : ['multi','puzzle','training_record','anniversary'].includes(request.mode) ? Array.from({length:request.account_count},(_,i)=>i+1)
        : request.mode === 'team' ? [...new Set([request.leader,...(request.friend_shortcut?[]:request.members)])] : [];
      const result = method === 'check_window'
        ? await N.call(method,{scope:slots.length?'tabs':'current',slots}) : await N.call(method);
      if (!result.ok) { toast(result.error || '当前操作未执行。', 'warning'); return; }
      if (result.path) toast('已保存：'+result.path);
      if (result.warning) toast(result.warning, 'warning');
      await N.refresh(true);
    } catch (error) { toast(error.message || String(error), 'warning'); }
  }

  async function nativeStart() {
    if (state.nativePending || state.settingsSaving || !N.ready || N.info?.busy) return;
    const token=++state.nativeStartToken;
    state.nativePending = true;
    render();
    try {
      await loadTaskOrder();
      await flushTaskOrder();
      if(token!==state.nativeStartToken)return;
      const result = await N.call('start',D.requestFor(state.config));
      if(token!==state.nativeStartToken){if(result.ok)await N.call('stop');return;}
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

  function configFromRequest(request, displayOrder=false) {
    const config = {...D.freshConfig(),...D.clone(state.config)};
    const mode = request.mode;
    config.section = mode === 'anniversary' ? 'anniversary' : ['solo','multi'].includes(mode) ? 'daily' : ['team','team_then_multi'].includes(mode) ? 'team' : 'independent';
    config.scope = mode === 'multi' ? 'multi' : 'current';
    config.dailyMode = ['solo','multi'].includes(mode) && request.tasks?.length === 1 && request.tasks[0] === 'training_record' ? 'training_record' : 'manual';
    config.teamMode = mode === 'team_then_multi' ? 'pipeline' : 'auto';
    config.independent = config.section === 'independent' ? mode : config.independent;
    const pairs = {tasks:'tasks',account_count:'accountCount',arena_wins:'arenaWins',formation:'formation',
      leader:'leader',members:'members',friend_shortcut:'friendShortcut',preformed_duos:'preformedDuos',preformed_pairs:'preformedPairs',preformed_trio:'preformedTrio',bond_count:'bondCount',
      bond_target_mode:'bondTargetMode',bond_kill_target:'bondKillTarget',bond_score_target:'bondScoreTarget',bond_team_goals:'bondTeamGoals',
      new_bond_round:'newBondRound',bond_stop_mode:'bondStopMode',final_managed:'finalManaged',arena_target_mode:'arenaTargetMode',
      escort_remaining:'escortRemaining',escort_prior_settled:'escortPriorSettled',training_arena_stop_policy:'trainingArenaStopPolicy',arena_second_skill:'arenaSecondSkill',grocery_items:'groceryItems'};
    for (const [source,target] of Object.entries(pairs)) if (request[source] !== undefined) config[target] = request[source];
    if(mode==='solo' && config.dailyMode!=='training_record' && Array.isArray(request.tasks)){
      config.currentTeamTasks=request.tasks.filter(code=>D.CURRENT_TEAM_TASKS.some(task=>task.code===code));
      config.tasks=request.tasks.filter(code=>D.SOLO_TASKS.some(task=>task.code===code));
      if(displayOrder)config.currentTaskOrder=[...request.tasks,...D.CURRENT_TASKS.map(task=>task.code).filter(code=>!request.tasks.includes(code))];
    }
    if(config.dailyMode==='training_record'){config.tasks=[...state.config.tasks];config.currentTeamTasks=[...state.config.currentTeamTasks];}
    if(!config.preformedTrio?.length)config.preformedTrio=state.config.preformedTrio?.length?state.config.preformedTrio:[1,2,3];
    if (mode === 'anniversary') config.tasks = D.freshConfig().tasks;
    config.pipelineAuto = !request.leaders?.length;
    config.pipelineLeaders = request.leaders?.length ? request.leaders.slice(0,3) : [1,3,null];
    if (mode === 'team_then_multi') config.pipelineTasks = request.team_tasks;
    else config.teamTasks = request.team_tasks || config.teamTasks;
    config.newBondRound = false; // The explicit new-round request is consumed once.
    config.escortPriorSettled = false; // A later partial request needs a fresh confirmation.
    config.taskOrder = state.config.taskOrder || D.defaultOrders();
    config.taskOrder = D.clone(config.taskOrder);
    const alignOrder = (group, selected) => {
      if (Array.isArray(selected)) config.taskOrder[group] = [...selected,...config.taskOrder[group].filter(code=>!selected.includes(code))];
    };
    if(displayOrder){
      alignOrder('solo',request.tasks?.filter(code=>D.SOLO_TASKS.some(task=>task.code===code)));
      alignOrder(mode === 'team_then_multi' ? 'pipeline' : 'team',request.team_tasks);
    }
    return config;
  }

  if (nativeMode) {
    window.addEventListener('hyol-native-state', ({detail:info}) => {
      loadTaskOrder();
      if (info.snapshot?.runId && state.nativeRunId !== info.snapshot.runId) {
        state.nativeRunId = info.snapshot.runId;
        if (info.request) state.config = configFromRequest(info.request);
        state.logClearedId = 0;
      }
      const snapshot = info.snapshot ? {...info.snapshot,plan:D.planFor(info.request ? configFromRequest(info.request,true) : state.config)} : null;
      if (snapshot) snapshot.logs = snapshot.logs.filter(row => row.id > state.logClearedId);
      state.snapshot = snapshot;
      state.scenario = snapshot ? 'native' : 'idle';
      const note = document.querySelector('.demo-notice-text');
      if (note) note.textContent = info.detail || '本地运行层已连接；点击开始后执行所选任务';
      const connection = document.querySelector('.connection-card div span');
      if (connection) connection.textContent = info.busy ? '本轮正在处理' : '运行层已连接';
      render();
      if(info.log_warning && info.log_warning!==state.logArchiveWarning){state.logArchiveWarning=info.log_warning;toast(info.log_warning,'warning');}
      if (info.close_notice && info.close_notice !== state.closeNotice) {
        state.closeNotice = info.close_notice;
        toast(info.close_notice,'warning');
      }
      const check = JSON.stringify(info.last_check);
      if (info.last_check && check !== state.lastCheck) {
        state.lastCheck = check;
        const got = info.last_check;
        const accounts = (got.accounts || []).map(row => `<div class="task-order-row"><span>标签 ${esc(row.tab_index)} · ${row.leaf_verified === true ? '已回木叶村' : row.status === 'offline' ? '掉线' : '待处理'}</span><span>${esc(readable(row.reason))}</span></div>`).join('');
        showDialog('help',`${dialogHeader(got.preparation ? '检查并回木叶村' : '只读窗口检查', '实际运行层返回的检查结果')}<div class="dialog-body"><div class="detail-grid"><div class="detail-item"><span>状态</span><strong>${esc(readable(got.status,'待核实'))}</strong></div><div class="detail-item"><span>窗口健康</span><strong>${got.healthy === true ? '是' : '尚未确认'}</strong></div><div class="detail-item"><span>木叶村确认</span><strong>${got.main_ready === true ? '已确认' : '尚未确认'}</strong></div><div class="detail-item"><span>${got.preparation ? '导航点击数' : '内容点击数'}</span><strong>${esc(got.navigation_clicks ?? got.content_clicks ?? '未知')}</strong></div></div>${accounts}<div class="reason-box" style="margin-top:16px">${esc(readable(got.reason || info.detail))}</div></div>`);
      }
      const leaders = JSON.stringify(info.leaders);
      if (info.leaders && leaders !== state.lastLeaders) {
        state.lastLeaders = leaders;
        const slots = info.leaders.leaders || [];
        if (slots.length) state.config.pipelineLeaders = slots.slice(0,3);
        state.detectedLeaders = slots.length ? '已核实队长标签：'+slots.join('、') : '未核实到队长，请先在游戏组成队伍。';
        render();
        toast(state.detectedLeaders,slots.length ? 'info' : 'warning');
      }
    });
    window.addEventListener('hyol-native-error',({detail}) => {
      const note = document.querySelector('.demo-notice-text');
      if (note) note.textContent = '界面连接暂未恢复，请稍后重试；当前任务状态保留。';
    });
  }

  function refreshLogList() {
    const stream = document.querySelector('.log-stream');
    if (stream) stream.innerHTML = renderLogRows();
    const count = document.getElementById('log-count');
    if (count) count.textContent = `${filteredLogs().length} / ${allLogs().length} 条 · 最近运行记录`;
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

  function activityDetails(code, rec) {
    const proofs = nativeMode ? `<div class="dialog-actions">${(rec.evidence_paths || []).map((path, index) => `<button class="button button-secondary button-small" data-action="open-evidence" data-evidence-index="${index}" title="${esc(path)}">证据 ${index + 1}</button>`).join('')}</div>` : '';
    if(code==='arena_3v3' && rec.arena3v3){
      const item=rec.arena3v3;
      const label={claimed:'已领取',available:'可领取',locked:'目标未达成',unknown:'待核实',pending:'领取待核实'};
      const rows=Object.entries(item.rewards||{}).map(([key,row])=>`<div class="detail-item"><span>${esc(row.label||key)}</span><strong>${row.current==null?'计数待核实':`${row.current} / ${row.target}`} · ${label[row.state]||'待核实'}</strong></div>`).join('');
      const boxes=Object.entries(item.chests||{}).map(([tier,row])=>`<div class="detail-item"><span>完成 ${esc(tier)} 项宝箱</span><strong>${label[row.state]||'待核实'}</strong></div>`).join('');
      return `<div class="dialog-section"><h3>竞技场3v3 · 本号任务与宝箱</h3><p>本轮领取 ${esc(item.script_claims||0)} 份；退出、无队和回村分别核实。</p><div class="detail-grid">${rows}${boxes}</div>${proofs}</div>`;
    }
    if (code === 'anniversary' && rec.anniversary) {
      const event = rec.anniversary;
      const days = (event.confirmed_days || []).join('、') || '尚未核实';
      return `<div class="dialog-section"><h3>限时活动 · 周年庆薪火签到</h3><p>本次领取 ${esc(event.claimed_now || 0)} 份；已核实第 ${esc(days)} 天。${event.next_day ? `第 ${esc(event.next_day)} 天待游戏解锁后继续。` : ''}</p>${proofs}</div>`;
    }
    if (['puzzle', 'weekend'].includes(code) && rec.puzzle?.difficulties) {
      const rows = [['easy', '简单'], ['normal', '普通'], ['hard', '困难']].map(([key, label]) => {
        const row = rec.puzzle.difficulties[key] || {};
        return `<div class="detail-item"><span>${label}</span><strong>${row.claimed === true ? '已领取' : row.solved === true ? '已通关，领奖待核实' : '尚未核实'}${row.phase ? ` · ${esc(readable(row.phase))}` : ''}</strong></div>`;
      }).join('');
      return `<div class="dialog-section"><h3>拼图三难度 · 活动日 ${esc(rec.puzzle.game_day || '待核实')}</h3><div class="detail-grid">${rows}</div>${proofs}</div>`;
    }
    if (code === 'training_record') {
      const before = rec.before?.rows || [];
      const after = rec.after?.rows || [];
      return `<div class="dialog-section"><h3>修行录目标复查</h3><p>执行前扫描 ${before.length} 项；执行后扫描 ${after.length} 项；${rec.all_goals_complete === true ? '全部目标已核实' : '仍有目标待核实或未支持'}。</p><p>本号日常：${esc(trainingPlanText(rec))}</p>${(rec.remaining || []).map((row) => `<div class="reason-box">${esc(row.description || row.text || '待核实目标')}${row.reason ? ` · ${esc(row.reason)}` : ''}</div>`).join('')}${proofs}</div>`;
    }
    return '';
  }

  function showDetail(slot, code) {
    const account = state.snapshot?.accounts.find((item) => item.slot === Number(slot));
    if (!account) return;
    const rec = account.tasks[code];
    state.detailSelection = { slot: Number(slot), code };
    const labels = {
      completed: '已核实轮次 / 次数', verified_battles: '已核实战斗总数', verified_first_round_battles: '已归档首轮战斗',
      verified_current_round_battles: '当前轮战斗', verified_defeats: '已核实失败场数', tickets_remaining: '战令剩余', remaining: '剩余次数', verified_wins: '已确认新增胜场', requested_wins: '目标胜场',
      phase: '当前阶段', requested: '目标场数', finished_at: '完成时间',
      actual_reward_claims: '本轮实际领取', reconciled_reward_claims: '原领取结果对账',
      preexisting_reward_rounds: '进入时已领取轮数', observed_complete_rounds: '观察到的满分轮数',
      bond_goal_label: '羁绊结束条件', bond_current_score: '最近确认积分', bond_current_kills: '最近确认击杀数',
    };
    const details = Object.entries(labels).filter(([key]) => rec[key] !== undefined).map(([key, label]) => `<div class="detail-item"><span>${label}</span><strong>${esc(key==='phase'?(rec.phase_label||readable(rec[key])):rec[key])}</strong></div>`).join('');
    const events = (state.snapshot.logs || []).filter((entry) => (entry.account === account.label || (account.label === '当前窗口' && entry.account === '系统')) && entry.task===code && entry.level!=='debug').slice(-4).reverse();
    const evidence = rec.evidence_paths || [];
    showDialog('detail', `${dialogHeader(`${account.label} · ${D.taskLabel(code)}`, `${account.identity}${nativeMode ? ' · 本轮核实记录' : ' · 界面演示数据'}`)}<div class="dialog-body"><div class="dialog-section"><div style="margin-bottom:13px">${statusBadge(rec.status, state.snapshot.status === 'not_open' && rec.status === 'skipped' ? '活动未开放' : '')}</div><div class="detail-grid"><div class="detail-item"><span>${rec.completion_scope==='current_account'?'本账号任务完成确认':'任务完成确认'}</span><strong>${rec.task_completed === true ? (rec.preexisting_complete?'既有完成，本次未重复执行':'是') : rec.outcome==='quota_exhausted'?'战令目标已达成，奖励条件不足':'否 / 尚未核实'}</strong></div><div class="detail-item"><span>${['bond','arena_3v3'].includes(code) && rec.ready_returned !== undefined ? '准备页确认' : '回村确认'}</span><strong>${code === 'managed' ? '不适用' : (['bond','arena_3v3'].includes(code) && rec.ready_returned !== undefined ? rec.ready_returned : rec.home_returned) === true ? '是' : '否 / 尚未核实'}</strong></div>${details}</div></div>
      ${rec.daily_observation?`<div class="dialog-section"><h3>日常检查</h3><p>${esc(rec.daily_observation.label)}</p><small>${esc(rec.daily_observation.observed_at||'')}</small><p>日常状态与本轮所选结束条件分别核验。</p></div>`:''}
      ${activityDetails(code, rec)}
      ${rec.last_error ? `<div class="dialog-section"><h3>待处理原因</h3><div class="reason-box">${esc(readable(rec.last_error))}${rec.error_suggestion?`<p>${esc(rec.error_suggestion)}</p>`:''}</div>${rec.error_diagnostic?`<details><summary>原始诊断</summary><pre>${esc(rec.error_diagnostic)}</pre></details>`:''}</div>` : ''}
      ${rec.pending_actions?.length ? `<div class="dialog-section"><h3>未决动作 · 禁止盲目重复</h3><p class="reason-box">${rec.pending_actions.length} 项原操作等待核实，未重复发送。</p><details><summary>原动作诊断</summary><div class="reason-box mono">${rec.pending_actions.map(esc).join('<br>')}</div></details></div>` : ''}
      <div class="dialog-section"><h3>已核实记录</h3><div class="detail-grid"><div class="detail-item"><span>结果记录</span><strong>${rec.verified_results?.length || 0} 条</strong></div><div class="detail-item"><span>领奖记录</span><strong>${rec.verified_claims?.length || 0} 条</strong></div></div></div>
      <div class="dialog-section"><h3>最近明细</h3>${events.length ? `<div class="event-timeline">${events.map((entry) => `<div class="event-item"><time>${esc(entry.time)}</time>${esc(entry.message)}</div>`).join('')}</div>` : '<p class="muted small">该环节尚无事件明细，保持未执行状态。</p>'}</div>
      <div class="dialog-section"><h3>本地证据</h3>${evidence.length ? `${nativeMode ? `<p class="muted small">本轮已保存 ${evidence.length} 张图片证据。</p>` : '<img class="evidence-thumb" src="assets/demo-evidence.svg" alt="界面演示证据示意图，非真实游戏截图">'}<div class="dialog-actions"><button class="button button-secondary button-small" data-action="open-evidence">${icon('eye')}${nativeMode ? '查看图片证据' : '查看演示证据'}</button></div>` : '<p class="muted small">该环节尚无可打开的截图；原状态保留，不会伪造完成凭证。</p>'}</div></div>`);
  }

  async function showEvidence(index) {
    const selection = state.detailSelection;
    const account = state.snapshot?.accounts.find((item) => item.slot === Number(selection?.slot));
    const rec = account?.tasks[selection?.code];
    const path = (Number.isInteger(index) ? rec?.evidence_paths?.[index] : rec?.evidence_paths?.at(-1)) || 'demo://evidence/unavailable.svg';
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
      showDialog('help', `${dialogHeader('使用说明', '本地桌面客户端')}<div class="dialog-body"><div class="help-lead">先在任务计划中选择账号范围、任务和参数，再点击开始。本轮进度与日志由现有任务运行层更新；完成必须有任务、领奖和回村核实。</div><div class="dialog-section"><h3>运行与停止</h3><p>点击开始会操作真实游戏。请先打开游戏并准备好需要的标签；运行中可点击停止或按F12。收到停止后等待本轮结束，再关闭窗口。</p></div><div class="dialog-section"><h3>状态与证据</h3><p>单个账号掉线不代表其他账号停止。待核实、未开放和跳过均不等于完成。进度读取失败会保留上次有效快照；图片只按本轮已记录路径查看。</p></div><div class="dialog-section"><h3>日志与配置</h3><p>业务日志和诊断各保留最近1000条，完整记录保留在日志目录。导出保存当前筛选的日志副本；清空显示不会删除磁盘记录。杂货屋仅购买手动勾选的月之印商品。完整中文记录自动保存在桌面“火影OL日志”。<button class="button button-secondary" data-action="onboarding">重新查看新人引导</button></p></div></div>`);
      return;
    }
    showDialog('help', `${dialogHeader('使用说明与安全边界', '新界面原型 · 仅界面演示数据')}<div class="dialog-body"><div class="help-lead">主流程是“任务计划 → 执行清单 → 本轮进度”。所有演示按钮只改变浏览器内的模拟状态，不导入原运行层，也不读取游戏、INI、个人日志或断点。</div><div class="help-list"><div class="help-item"><span>1</span><div><strong>先确认范围，再开始</strong><p>当前账号或最多六个标签、两种组队流程、四种独立任务都在任务计划中。右侧清单按真实顺序列出任务与关键参数。</p></div></div><div class="help-item"><span>2</span><div><strong>开始 / 停止只控制演示</strong><p>开始会创建新演示运行并推进模拟状态；停止会先显示“停止中”，再转为“已停止”。浏览器中可按 F12 尝试同样的演示停止，正式全局 F12 仍须由原运行层实现。</p></div></div><div class="help-item"><span>3</span><div><strong>状态必须带原因与证据</strong><p>点击任务单元格可查看完成、回村、未决动作和证据。掉线、待核实、活动未开放与刷新失败均不等于完成；刷新失败保留上次有效快照。</p></div></div><div class="help-item"><span>4</span><div><strong>日志工具</strong><p>支持全文搜索、级别与账号筛选、自动滚动和导出演示文本。“清空显示”不删除磁盘日志；演示模式不会打开个人日志目录。</p></div></div></div><div class="dialog-section" style="margin-top:18px"><h3>保持不变的现有边界</h3><ul class="boundary-list"><li>账号身份、任务完成、领奖与回村仍须真实核实。</li><li>F12 紧急停止、购买禁用、识别阈值、INI 与断点格式不在 UI 重写。</li><li>当前产品只展示本轮进度，不提供旧任务恢复、批次切换或历史累计。</li><li>原型未连接真实 RuntimeController，未进行实机验收。</li></ul></div></div>`);
  }

  function showWindowCheck() {
    if (nativeMode) { nativeCommand('check_window'); return; }
    showDialog('help', `${dialogHeader('检查并回木叶村 · 演示结果', '没有读取或点击任何真实窗口')}<div class="dialog-body"><div class="help-lead">正式检查会按所选范围关闭已核实普通面板并回村，逐号显示实际导航次数。此处为界面演示。</div><div class="dialog-actions"><button class="button button-primary" data-action="close-dialog">知道了</button></div></div>`);
  }

  function showRequest() {
    const request = D.requestFor(state.config);
    showDialog('help', `${dialogHeader(nativeMode ? '本轮任务参数' : '演示 RunRequest', nativeMode ? '开始时由现有运行层再次校验' : '只生成预览，不提交给 RuntimeController')}<div class="dialog-body"><pre class="request-preview">${esc(JSON.stringify(request, null, 2))}</pre><div class="dialog-actions"><button class="button button-primary" data-action="close-dialog">关闭</button></div></div>`);
  }

  function startDemo() {
    if(state.onboarding?.required||state.tour){openOnboarding();return;}
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
    if(nativeMode && state.nativePending){state.nativeStartToken++;state.nativePending=false;render();if(N.info?.busy)nativeCommand('stop');else toast('已取消开始，本轮尚未执行。');return;}
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

  async function exportNativeLogs() {
    try {
      const result=await N.call('export_logs',{level:state.logLevel,account:state.logAccount,task:state.logTask,query:state.logSearch,after_id:state.logClearedId});
      if(!result.ok){toast(result.error||'日志导出失败。','warning');return;}
      toast(`已导出当前筛选的 ${result.count} 条日志，已保存到桌面的火影OL日志文件夹。`);
    } catch(error){toast('日志导出失败，请检查客户端目录权限。','warning');}
  }
  function exportLogs() {
    if (nativeMode) { exportNativeLogs(); return; }
    const lines = filteredLogs().map(logLine);
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
    const dailyMode = event.target.closest('[data-daily-mode]');
    if (dailyMode) {
      state.config.dailyMode = dailyMode.dataset.dailyMode;
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
    if(action==='select-task'){state.taskSettings=button.dataset.taskCode;state.advancedOpen=true;render();requestAnimationFrame(()=>main.querySelector('[data-advanced]')?.scrollIntoView({block:'start'}));return;}
    if(action==='copy-logs'){navigator.clipboard.writeText(filteredLogs().map(logLine).join('\n')).then(()=>toast('日志已复制')).catch(()=>toast('复制失败，请使用导出日志','warning'));return;}
    if (action === 'onboarding') {openOnboarding();return;}
    if (action === 'tour-skip') {skipTour();return;}
    if (action === 'tour-next') {advanceTour();return;}
    if (action === 'tour-back') {if(!state.tour||state.tour.busy)return;state.tour.index=Math.max(0,state.tour.index-1);render();return;}
    if (action === 'help') showHelp();
    if (action === 'check-window') showWindowCheck();
    if (action === 'save-settings') saveSettings();
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
      const source=catalogFor(group);
      setSelection(group,action==='clear-tasks'?[]:source.filter(task=>task.default).map(task=>task.code));
      if (action === 'default-tasks') {
        setOrder(group,group==='current'?[...D.defaultOrders().solo,...D.CURRENT_TEAM_TASKS.map(task=>task.code)]:D.defaultOrders()[group]);
      }
      render();
    }
    if (action === 'task-up' || action === 'task-down') {
      const group = button.dataset.taskGroup;
      const source=catalogFor(group);
      state.config.taskOrder = state.config.taskOrder || D.defaultOrders();
      const order = D.orderedCatalog(state.config,group,source);
      const selected = order.filter(code => selectionFor(group).includes(code));
      if(button.dataset.taskCode==='arena_3v3')return;
      const at = selected.indexOf(button.dataset.taskCode), other = at+(action==='task-up'?-1:1);
      if (at>=0 && other>=0 && other<selected.length && selected[other]!=='arena_3v3') {
        const left = order.indexOf(selected[at]), right = order.indexOf(selected[other]);
        [order[left],order[right]] = [order[right],order[left]];
        setOrder(group,order);
        render();
      }
    }
    if (action === 'detect-leaders') {
      if (nativeMode) { nativeCommand('detect_leaders'); return; }
      state.config.pipelineLeaders = [1, 3, 5];
      state.detectedLeaders = '演示识别结果：队长标签 1、6；内容点击数 0。未读取真实窗口。';
      render();
      toast('已填入演示队长标签；未执行真实识别。');
    }
    if (action === 'open-detail') showDetail(button.dataset.account, button.dataset.task);
    if (action === 'open-evidence') showEvidence(button.dataset.evidenceIndex === undefined ? undefined : Number(button.dataset.evidenceIndex));
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
      if(nativeMode)state.logClearedId=Math.max(state.logClearedId,...allLogs().map(row=>row.id||0));
      if(state.snapshot)state.snapshot.logs=[];
      render(); toast(nativeMode ? '已清空当前日志显示，磁盘记录保留。' : '仅清空了当前演示日志显示，未删除任何磁盘文件。');
    }
    if (action === 'clear-log-filters') { state.logSearch = ''; state.logLevel = 'all'; state.logAccount = 'all'; state.logTask='all'; render(); }
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
    if(target.id==='log-task'){state.logTask=target.value;refreshLogList();return;}
    if (target.id === 'log-account') { state.logAccount = target.value; refreshLogList(); return; }
    if (target.id === 'auto-scroll') { state.autoScroll = target.checked; return; }
    if(target.dataset.trioIndex!==undefined){state.config.preformedTrio[Number(target.dataset.trioIndex)]=Number(target.value);render();return;}
    if (target.dataset.preformedMember !== undefined) {
      const leader = Number(target.dataset.preformedMember);
      state.config.preformedPairs = state.config.preformedPairs.filter(pair=>pair[0]!==leader);
      if (target.value) state.config.preformedPairs.push([leader,Number(target.value)]);
      render(); return;
    }
    if (target.dataset.bondTeamMode !== undefined) {
      const leader = Number(target.dataset.bondTeamMode);
      state.config.bondTeamGoals = state.config.bondTeamGoals.filter(row => row[0] !== leader);
      if (target.value !== 'inherit') state.config.bondTeamGoals.push([leader,target.value,({battles:5,kills:160,score:100})[target.value]]);
      render(); return;
    }
    if (target.dataset.bondTeamTarget !== undefined) {
      const row = state.config.bondTeamGoals.find(item => item[0] === Number(target.dataset.bondTeamTarget));
      if (row) row[2] = Number(target.value);
      render(); return;
    }
    if (target.dataset.pipelineLeader !== undefined) {
      state.config.pipelineLeaders[Number(target.dataset.pipelineLeader)] = target.value ? Number(target.value) : '';
      render();
      return;
    }
    if(target.dataset.groceryItem){
      const code=target.dataset.groceryItem,old=state.config.groceryItems||[];
      state.config.groceryItems=target.checked?[...new Set([...old,code])]:old.filter(x=>x!==code);
      render();return;
    }
    if (target.dataset.taskCode) {
      const group = target.dataset.taskGroup;
      const code = target.dataset.taskCode;
      const old=selectionFor(group),selected=old.includes(code);
      setSelection(group,target.checked?[...old,code]:old.filter(item=>item!==code));
      if(group==='pipeline'&&code==='arena_3v3'&&target.checked){state.config.formation='keep';if(!state.config.preformedTrio?.length)state.config.preformedTrio=[1,2,3];state.config.preformedDuos=false;state.config.preformedPairs=[];state.config.pipelineTasks=state.config.pipelineTasks.filter(t=>t!=='bond');}
      if(group==='pipeline'&&code==='bond'&&target.checked){state.config.pipelineTasks=state.config.pipelineTasks.filter(t=>t!=='arena_3v3');}
      if (selected === target.checked) return;
      render();
      return;
    }
    if (target.dataset.config) {
      const key = target.dataset.config;
      let value = target.type === 'checkbox' ? target.checked : target.value;
      if (['accountCount', 'arenaWins', 'bondCount', 'bondKillTarget', 'bondScoreTarget', 'leader', 'escortRemaining'].includes(key)) value = Number(value);
      state.config[key] = value;
      if (key === 'preformedDuos' && value) state.config.pipelineAuto = false;
      if (key === 'escortRemaining') state.config.escortPriorSettled = false;
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
  let draggedQueue=null;
  document.addEventListener('dragstart',event=>{
    const row=event.target.closest('[data-queue-drag]')?.closest('[data-queue-code]');
    if(!row||row.dataset.queueCode==='arena_3v3'||(nativeMode&&N.info?.busy)){event.preventDefault();return;}
    draggedQueue={group:row.dataset.queueGroup,code:row.dataset.queueCode};
    event.dataTransfer.setData('text/plain',draggedQueue.code);event.dataTransfer.effectAllowed='move';
  });
  document.addEventListener('dragover',event=>{const row=event.target.closest('[data-queue-code]');if(row&&draggedQueue&&row.dataset.queueGroup===draggedQueue.group){event.preventDefault();event.dataTransfer.dropEffect='move';}});
  document.addEventListener('drop',event=>{
    const row=event.target.closest('[data-queue-code]');
    if(!row||!draggedQueue||row.dataset.queueGroup!==draggedQueue.group||row.dataset.queueCode==='arena_3v3')return;
    event.preventDefault();
    const group=draggedQueue.group,source=catalogFor(group);
    const order=D.orderedCatalog(state.config,group,source).filter(code=>code!==draggedQueue.code);
    order.splice(order.indexOf(row.dataset.queueCode),0,draggedQueue.code);
    setOrder(group,order);draggedQueue=null;render();
  });
  document.addEventListener('dragend',()=>{draggedQueue=null;});
  setInterval(()=>{
    const snap=state.snapshot;
    const elapsed=Math.max(0,Math.floor(snap?.elapsedSeconds||0));
    document.querySelectorAll('[data-live-elapsed]').forEach(el=>{el.textContent=snap?`本轮运行 ${Math.floor(elapsed/60)} 分 ${elapsed%60} 秒 · ${snap.timerRunning?'正在执行或等待任务结果':'当前操作已结束'}`:'开始后显示实际运行耗时';});
  },1000);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'F12' && (nativeMode ? (N.info?.busy||state.nativePending) : state.snapshot?.status === 'running')) {
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
  if (!nativeMode && !params.get('scenario')) {
    try {
      const saved=JSON.parse(localStorage.getItem('hyol-desktop-settings') || 'null');
      if(saved && typeof saved==='object')state.config={...D.freshConfig(),...saved,newBondRound:false,escortPriorSettled:false};
    } catch (_) { /* Keep the default plan when preview storage is unavailable. */ }
  }
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
