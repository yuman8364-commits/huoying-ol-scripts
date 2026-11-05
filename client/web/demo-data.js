(() => {
  const SOLO_TASKS = [
    { code: 'mail', label: '邮件', description: '领取邮件附件，背包满时明确提示清理', default: true },
    { code: 'escort', label: '押镖', description: '按本日剩余次数，优先免费SS；无SS先免费刷新，额度耗尽后铜币；11–14点、18–21点', default: true },
    { code: 'welfare', label: '领取奖励', description: '领取当前可领取的在线福利', default: true },
    { code: 'daruma', label: '达摩', description: '连续确认奖励次数归零后回村', default: false },
    { code: 'ranking', label: '排位赛', description: '挑战第一位，核实剩余次数归零', default: true },
    { code: 'survival', label: '生存演习', description: '沿用现有战斗、领奖与补跑判定', default: true },
    { code: 'quiz', label: '忍者测验', description: '全部选择第一项，核实完成与回村', default: true },
    { code: 'arena', label: '竞技场', description: '按实际战令或所选目标结束', default: true },
    { code: 'puzzle', label: '忍者拼图', description: '原拼图程序完成三个难度，已领取项不重开', default: false },
    { code: 'weekend', label: '周末任务', description: '拼图三个难度与达摩，仅在开放日执行', default: false },
    { code: 'treasure', label: '挖宝', description: '每账号固定 5 分钟，仅使用已有地图', default: false },
    { code: 'training_record', label: '修行录目标', description: '先领完奖励，再按各账号缺项安排日常并逐项更新', default: false },
  ];
  const TEAM_TASKS = [
    { code: 'group_instance', label: '组队副本', description: '今日奖励与友情奖励均核实归零', default: true },
    { code: 'wanted', label: '通缉', description: '核实通缉任务列表清空', default: true },
    { code: 'strong_approach', label: '强者', description: '奖励归零或连续确认体力不足', default: true },
    { code: 'sealed', label: '封印忍者', description: '沿用现有每日次数与完成判定', default: false },
    { code: 'rescue_jinchuriki', label: '营救人柱力', description: '仅在活动开放且奖励免费时执行', default: false },
    { code: 'team_pvp', label: '组队竞技', description: '仅执行现有免费奖励流程', default: false },
  ];
  const PIPELINE_TASKS = [...TEAM_TASKS.filter((task) => ['group_instance', 'wanted', 'strong_approach'].includes(task.code)),
    { code: 'bond', label: '羁绊（多人完成后）', description: '仅二人小队；每场核实成员、结算与返回', default: false }];
  const INDEPENDENT_TASKS = [
    { code: 'arena', label: '竞技场', description: '按实际战令或所选目标结束', icon: 'arena' },
    { code: 'bond', label: '羁绊', description: '一直打、打5次或5场胜利', icon: 'bond' },
    { code: 'weekend', label: '周末任务', description: '拼图三难度与达摩', icon: 'weekend' },
    { code: 'treasure', label: '挖宝', description: '每账号固定 5 分钟', icon: 'treasure' },
    { code: 'puzzle', label: '忍者拼图', description: '当前账号完成简单、普通、困难', icon: 'weekend' },
    { code: 'training_record', label: '修行录目标', description: '当前账号领奖、识别缺项并补日常', icon: 'logs' },
  ];
  const ANNIVERSARY_TASKS = [
    { code: 'anniversary', label: '薪火签到', description: '按已解锁进度领取10天奖励；本期未检测到时静默略过' },
  ];
  const TASK_MAP = Object.fromEntries([...SOLO_TASKS, ...TEAM_TASKS, ...ANNIVERSARY_TASKS].map((task) => [task.code, task]));
  TASK_MAP.managed = { code: 'managed', label: '最后托管', description: '按游戏当前设置进入并确认托管' };
  TASK_MAP.bond = { code: 'bond', label: '羁绊', description: '完成已核实战斗后，按原轮次收尾' };
  const STATUS = {
    new: { label: '未执行', tone: 'neutral' },
    running: { label: '执行中', tone: 'info' },
    done: { label: '已完成', tone: 'success' },
    pending: { label: '待核实', tone: 'warning' },
    skipped: { label: '跳过', tone: 'neutral' },
    unselected: { label: '未选择', tone: 'neutral' },
    offline: { label: '掉线', tone: 'danger' },
  };
  const RUN_STATUS = {
    idle: { label: '未开始', title: '就绪，等待开始', tone: 'neutral' },
    running: { label: '执行中', title: '本轮正在执行', tone: 'info' },
    stopping: { label: '停止中', title: '正在结束本轮', tone: 'warning' },
    stopped: { label: '已停止', title: '本轮已停止', tone: 'neutral' },
    pending: { label: '待处理', title: '本轮仍有待处理项', tone: 'warning' },
    complete: { label: '已完成', title: '本轮所选任务已完成', tone: 'success' },
    not_open: { label: '活动未开放', title: '所选活动当前未开放', tone: 'warning' },
  };
  const DEFAULT_CONFIG = {
    section: 'daily',
    dailyMode: 'manual',
    scope: 'current',
    accountCount: 6,
    tasks: SOLO_TASKS.filter((task) => task.default).map((task) => task.code),
    teamMode: 'auto',
    leader: 1,
    members: [2, 3],
    friendShortcut: false,
    teamTasks: TEAM_TASKS.filter((task) => task.default).map((task) => task.code),
    pipelineTasks: ['group_instance', 'wanted', 'strong_approach'],
    pipelineAuto: true,
    preformedDuos: false,
    preformedPairs: [],
    pipelineLeaders: [1, 3, null],
    independent: 'arena',
    arenaTargetMode: 'tickets',
    arenaWins: 5,
    formation: 'keep',
    bondCount: 5,
    bondStopMode: 'battles',
    bondTargetMode: 'battles',
    bondKillTarget: 160,
    bondScoreTarget: 100,
    bondTeamGoals: [],
    newBondRound: false,
    finalManaged: true,
    escortRemaining: 0,
    escortPriorSettled: false,
  };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const freshConfig = () => clone(DEFAULT_CONFIG);
  const taskLabel = (code) => TASK_MAP[code]?.label || code;
  const defaultOrders = () => ({
    solo: [...['mail','escort','ranking','survival','quiz','arena'], ...SOLO_TASKS.map(t => t.code).filter(t => !['mail','escort','ranking','survival','quiz','arena','welfare'].includes(t)), 'welfare'],
    team: TEAM_TASKS.map(t => t.code), pipeline: PIPELINE_TASKS.map(t => t.code),
  });
  const orderedCatalog = (config, group, source) => {
    const allowed = source.map(t => t.code);
    const order = config.taskOrder?.[group] || defaultOrders()[group];
    return [...new Set([...order, ...allowed])].filter(code => allowed.includes(code));
  };
  const selectedOrdered = (selected, source, config, group) => {
    const chosen = orderedCatalog(config, group, source).filter(code => selected.includes(code));
    return group === 'pipeline' ? [...chosen.filter(code => code !== 'bond'), ...chosen.filter(code => code === 'bond')] : chosen;
  };
  const selectedSoloTasks = (config) => selectedOrdered(config.tasks, SOLO_TASKS, config, 'solo');

  function requestFor(config) {
    const mode = config.section === 'daily'
      ? (config.scope === 'multi' ? 'multi' : 'solo')
      : config.section === 'team'
        ? (config.teamMode === 'pipeline' ? 'team_then_multi' : 'team')
        : config.section === 'anniversary' ? 'anniversary' : config.independent;
    return {
      mode,
      tasks: config.section === 'anniversary' ? ['anniversary'] : config.section === 'daily' && config.dailyMode === 'training_record' ? ['training_record'] : selectedSoloTasks(config),
      account_count: config.section === 'independent' ? 1 : mode === 'team_then_multi' ? 6 : Number(config.accountCount),
      arena_wins: Number(config.arenaWins),
      formation: config.section === 'anniversary' ? 'keep' : config.formation,
      leader: Number(config.leader),
      leaders: mode === 'team_then_multi' && !config.pipelineAuto
        ? config.pipelineLeaders.filter(Boolean).map(Number)
        : [],
      members: config.members.map(Number).sort((a, b) => a - b),
      friend_shortcut: Boolean(config.friendShortcut),
      preformed_duos: mode === 'team_then_multi' && Boolean(config.preformedDuos),
      preformed_pairs: mode === 'team_then_multi' && config.preformedDuos ? config.preformedPairs.map(row => [...row]) : [],
      team_tasks: mode === 'team_then_multi'
        ? selectedOrdered(config.pipelineTasks, PIPELINE_TASKS, config, 'pipeline')
        : selectedOrdered(config.teamTasks, TEAM_TASKS, config, 'team'),
      bond_count: mode === 'bond' ? 5 : Number(config.bondCount),
      bond_stop_mode: mode === 'bond' ? config.bondStopMode : 'battles',
      bond_target_mode: mode === 'team_then_multi' ? config.bondTargetMode : 'battles',
      bond_kill_target: Number(config.bondKillTarget),
      bond_score_target: Number(config.bondScoreTarget),
      bond_team_goals: mode === 'team_then_multi' ? config.bondTeamGoals.map(row => [...row]) : [],
      new_bond_round: mode === 'bond' && Boolean(config.newBondRound),
      final_managed: mode !== 'bond' && mode !== 'anniversary' && Boolean(config.finalManaged),
      arena_target_mode: config.arenaTargetMode,
      escort_remaining: Number(config.escortRemaining),
      escort_prior_settled: Boolean(config.escortRemaining) && Boolean(config.escortPriorSettled),
    };
  }

  function validate(config) {
    const request = requestFor(config);
    if (request.mode === 'bond' && !['endless','battles','wins'].includes(request.bond_stop_mode)) return '请选择羁绊目标：一直打、打5次或5场胜利。';
    if (['solo', 'multi'].includes(request.mode) && !request.tasks.length) {
      return '请至少选择一项单人任务。';
    }
    if (request.escort_remaining && (request.mode !== 'solo' || request.tasks.length !== 1
        || request.tasks[0] !== 'escort' || request.final_managed || !request.escort_prior_settled)) {
      return '续做剩余押镖须仅选当前账号的押镖，确认此前已交付，并关闭最后托管。';
    }
    if (['multi', 'puzzle', 'training_record', 'anniversary'].includes(request.mode) && (!Number.isInteger(request.account_count) || request.account_count < 1 || request.account_count > 6)) {
      return '多账号数量须为 1～6。';
    }
    if (request.arena_wins < 1 || request.arena_wins > 10) {
      return '竞技场辅助胜场数须为 1～10。';
    }
    if (request.mode === 'team') {
      if (!request.team_tasks.length) return '请至少选择一项组队任务。';
      if (!request.friend_shortcut && (!request.members.length || request.members.length > 2)) {
        return '请选择 1 或 2 个队员标签。';
      }
      if (!request.friend_shortcut && request.members.includes(request.leader)) {
        return '队长与队员标签不能重复。';
      }
    }
    if (request.mode === 'team_then_multi') {
      if (!request.team_tasks.length) return '请至少选择副本、通缉、强者或二人羁绊。';
      if (request.preformed_duos && !request.preformed_pairs.length && request.leaders.some(slot => ![1,3,5].includes(slot))) return '请填写各队当前队员标签。';
      if (request.preformed_duos && (!request.leaders.length || !request.preformed_pairs.length)) return '请指定当前各队的队长和队员标签。';
      if (!config.pipelineAuto && !request.leaders.length) return '请至少选择第一队队长，或开启自动发现。';
      if (new Set(request.leaders).size !== request.leaders.length) return '各队队长标签不能重复。';
    }
    if (request.preformed_pairs.length) {
      const used = new Set();
      for (const pair of request.preformed_pairs) {
        if (pair.length !== 2 || pair.some(slot => !Number.isInteger(slot) || slot < 1 || slot > 6 || used.has(slot)) || pair[0] === pair[1]) return '二人队标签不能重复或跨队重叠。';
        pair.forEach(slot => used.add(slot));
      }
      if (request.leaders.length && (request.preformed_pairs.length !== request.leaders.length || request.preformed_pairs.some(pair => !request.leaders.includes(pair[0])))) return '请填写每支所选队伍的队员标签。';
    }
    if ((request.mode === 'bond' || request.team_tasks.includes('bond')) && (!Number.isInteger(request.bond_count) || request.bond_count < 1 || request.bond_count > 99)) {
      return '羁绊场数须为 1～99。';
    }
    if (!['battles','kills','score'].includes(request.bond_target_mode)) return '请选择羁绊结束条件。';
    if (!Number.isInteger(request.bond_kill_target) || request.bond_kill_target < 0 || request.bond_kill_target > 999) return '击杀宝箱档位须为0～999；0表示160人档也领取完成。';
    if (!Number.isInteger(request.bond_score_target) || request.bond_score_target < 0 || request.bond_score_target > 99999) return '掉分目标须为0～99999。';
    const seen = new Set();
    for (const [leader,mode,value] of request.bond_team_goals) {
      if (!Number.isInteger(leader) || leader < 1 || leader > 6 || seen.has(leader)
          || (!config.pipelineAuto && !request.leaders.includes(leader))) return '每队目标须对应本轮选择的队长。';
      if (!['battles','kills','score'].includes(mode) || !Number.isInteger(value)
          || value < (mode === 'battles' ? 1 : 0) || value > ({battles:99,kills:999,score:99999})[mode]) return '每队羁绊目标数值无效。';
      seen.add(leader);
    }
    return '';
  }

  function planFor(config) {
    const request = requestFor(config);
    let phases = [];
    let accounts = [];
    let title = '';
    if (request.mode === 'anniversary') {
      title = `周年庆活动 · 前 ${request.account_count} 个标签`;
      accounts = Array.from({ length: request.account_count }, (_, index) => ({ slot: index + 1, label: `标签 ${index + 1}`, identity: `演示身份 ${String(index + 1).padStart(2, '0')}` }));
      phases = [{ label: '核对限时入口 → 领取已解锁奖励 → 确认回村', tasks: ['anniversary'] }];
    } else if (['solo', 'multi'].includes(request.mode)) {
      title = request.mode === 'solo' ? '当前账号 · 单人任务' : `多账号 · 前 ${request.account_count} 个标签`;
      accounts = request.mode === 'solo'
        ? [{ slot: 1, label: '当前窗口', identity: '演示身份 · 当前账号' }]
        : Array.from({ length: request.account_count }, (_, index) => ({ slot: index + 1, label: `标签 ${index + 1}`, identity: `演示身份 ${String(index + 1).padStart(2, '0')}` }));
      const training = request.tasks.length === 1 && request.tasks[0] === 'training_record';
      if (training) title += ' · 按修行录补任务';
      phases = [{ label: training ? '领完可领奖励 → 重新识别缺项 → 逐号定制日常 → 逐项更新' : '按现有顺序执行单人任务', tasks: request.tasks }];
    } else if (request.mode === 'team') {
      const slots = request.friend_shortcut
        ? [request.leader, ...[1, 2, 3, 4, 5, 6].filter((slot) => slot !== request.leader).slice(0, 2)]
        : [request.leader, ...request.members];
      accounts = [...new Set(slots)].sort((a, b) => a - b).map((slot) => ({ slot, label: `标签 ${slot}`, identity: `演示身份 ${String(slot).padStart(2, '0')}` }));
      title = request.friend_shortcut ? `自动建队 · 标签 ${request.leader} 为队长 · 好友邀请 2 人` : `自动建队 · 队长 ${request.leader} / 队员 ${request.members.join('、')}`;
      phases = [{ label: '建队并完成组队活动', tasks: request.team_tasks }];
    } else if (request.mode === 'team_then_multi') {
      title = config.pipelineAuto ? '已有队伍 · 自动发现队长 · 最多 6 个标签' : `已有队伍 · 队长标签 ${request.leaders.join('、')}`;
      accounts = Array.from({ length: 6 }, (_, index) => ({ slot: index + 1, label: `标签 ${index + 1}`, identity: `演示身份 ${String(index + 1).padStart(2, '0')}` }));
      phases = [
        { label: '阶段 1 · 已核实队伍', tasks: request.team_tasks },
        ...(request.tasks.length ? [{ label: '阶段 2 · 解散后单人任务', tasks: request.tasks }] : []),
      ];
    } else {
      title = `独立任务 · ${taskLabel(request.mode)} · 当前窗口`;
      accounts = [{ slot: 1, label: '当前窗口', identity: '演示身份 · 当前账号' }];
      phases = [{ label: '独立执行，不与日常任务合并', tasks: [request.mode] }];
    }
    if (request.final_managed && request.mode !== 'bond') {
      const managedPhase = request.mode === 'bond' ? '按羁绊原轮次核实回村与最后托管' : '所选任务与回村核实后';
      phases.push({ label: managedPhase, tasks: ['managed'], finish: true });
    }
    const taskCodes = [...new Set(phases.flatMap((phase) => phase.tasks))];
    return { request, title, accounts, phases, taskCodes };
  }

  function detailFor(code, config) {
    if (code === 'anniversary') return '薪火签到10天 · 已领取不重复 · 后续解锁后继续 · 未检测到静默略过';
    if (code === 'arena') {
      return config.arenaTargetMode === 'wins' ? `目标：新增 ${config.arenaWins} 个已确认胜场` : '目标：已核实战令次数归零';
    }
    if (code === 'bond' && config.section === 'team') {
      const label = config.bondTargetMode === 'kills' ? (config.bondKillTarget ? `击杀宝箱领取至 ${config.bondKillTarget} 人档` : '160人档也领取完成')
        : config.bondTargetMode === 'score' ? `积分降到 ≤${config.bondScoreTarget}` : `每队 ${config.bondCount} 场`;
      return `两人均达标后结束 · ${label}${config.bondTeamGoals.length ? ' · 含单队目标' : ''}`;
    }
    if (code === 'bond') return `${config.section === 'independent' ? ({endless:'一直打，手动停止',battles:'打5次，胜负均计',wins:'5场胜利'})[config.bondStopMode] : `目标 ${config.bondCount} 场`} · ${config.newBondRound ? '开始新一轮' : '继续原轮次'}`;
    if (code === 'treasure') return '每账号固定 5 分钟 · 到时暂停并回村 · 不购买地图';
    if (code === 'weekend') return '拼图三个难度 + 达摩 · 未开放时不计完成';
    if (code === 'puzzle') return '每号三个难度 · 05:00刷新 · 已领取项只复核';
    if (code === 'training_record') return '先领完奖励 · 逐号计算缺项 · 每完成一项更新日常';
    if (code === 'managed') return config.independent === 'bond' && config.section === 'independent' ? '沿用当前默认开启 · 与羁绊收尾绑定' : '沿用游戏当前设置 · 完成后核实托管状态';
    return TASK_MAP[code]?.description || '';
  }

  function record(status, code, extras = {}) {
    const base = {
      status,
      task_completed: status === 'done',
      home_returned: status === 'done' && code !== 'managed' ? true : undefined,
      pending_actions: [],
      evidence_paths: status === 'done' ? [`demo://evidence/${code}-verified.svg`] : [],
      verified_claims: status === 'done' && !['managed', 'bond'].includes(code) ? [`${code}_reward_claim_verified`] : [],
      verified_results: status === 'done' ? [`${code}_result_confirmed`] : [],
      events: [],
      last_error: '',
    };
    if (code === 'managed' && status === 'done') base.evidence = 'demo://evidence/managed-verified.svg';
    if (code === 'survival' && status === 'done') Object.assign(base, { verified_battles: 6, verified_first_round_battles: 3, verified_current_round_battles: 3 });
    if (code === 'arena' && status === 'done') Object.assign(base, { remaining: 0, verified_wins: 2 });
    if (code === 'bond' && status === 'done') Object.assign(base, { requested: 5, completed: 5, phase: 'ready' });
    if (status === 'done') base.finished_at = '2026-10-24 14:34:18';
    if (status === 'running') Object.assign(base, { task_completed: false, home_returned: false, phase: '执行与结果核实', evidence_paths: [], verified_claims: [], verified_results: [] });
    return Object.assign(base, extras);
  }

  const BASE_LOGS = [
    { time: '14:31:02', level: 'info', account: '系统', event: 'unified_run_started', message: '已创建本轮演示运行；仅处理界面演示数据。' },
    { time: '14:31:03', level: 'success', account: '系统', event: 'unified_window_check', message: '只读窗口预检通过：画面健康，内容点击数为 0。' },
    { time: '14:31:05', level: 'info', account: '标签 1', event: 'account_bound', message: '本轮标签与演示身份已绑定；后续操作前重新核实。' },
    { time: '14:31:08', level: 'info', account: '标签 1', event: 'multi_solo_task_started', message: '邮件任务开始。' },
    { time: '14:31:16', level: 'success', account: '标签 1', event: 'multi_solo_task_complete', message: '邮件附件领取、任务结果与回村均已确认。' },
    { time: '14:31:21', level: 'success', account: '标签 1', event: 'multi_solo_task_complete', message: '在线福利领奖与回村已确认。' },
    { time: '14:31:30', level: 'success', account: '标签 1', event: 'multi_solo_task_complete', message: '排位赛剩余次数已核实归零，回村已确认。' },
    { time: '14:31:34', level: 'info', account: '标签 1', event: 'multi_solo_task_started', message: '生存演习开始；战斗次数只按已确认结果记录。' },
    { time: '14:31:36', level: 'info', account: '标签 2', event: 'multi_solo_task_started', message: '邮件任务开始；其他账号按演示调度继续。' },
    { time: '14:31:39', level: 'info', account: '标签 2', event: 'combat_battle_start_requested', message: '已发送开战请求，等待新画面确认，不以点击成功代替战斗结果。' },
  ];

  function makeSnapshot(config, kind = 'running') {
    const plan = planFor(config);
    const accounts = plan.accounts.map((account) => ({
      ...account,
      health: '已绑定',
      tasks: Object.fromEntries(plan.taskCodes.map((code) => [code, record('new', code)])),
    }));
    const codes = plan.taskCodes;
    const runningPattern = (account, index) => {
      const doneCount = codes.length < 2 ? 0 : [3, 1, 2, 0, codes.length, 0][index % 6];
      codes.forEach((code, taskIndex) => {
        if (taskIndex < Math.min(doneCount, codes.length)) account.tasks[code] = record('done', code);
      });
      if ([0, 1, 3].includes(index) && doneCount < codes.length) {
        account.tasks[codes[doneCount]] = record('running', codes[doneCount]);
      }
    };
    if (['running', 'stopping', 'stopped', 'offline', 'refresh_failed'].includes(kind)) {
      accounts.forEach(runningPattern);
    }
    if (kind === 'fresh') {
      if (accounts[0] && codes[0]) accounts[0].tasks[codes[0]] = record('running', codes[0]);
    }
    if (kind === 'stopped') {
      accounts.forEach((account) => codes.forEach((code) => {
        if (account.tasks[code].status === 'running') {
          account.tasks[code] = record('pending', code, { last_error: '运行已停止，完成条件尚未核实；未重复提交原动作。' });
        }
      }));
    }
    if (kind === 'pending' || kind === 'complete') {
      accounts.forEach((account) => codes.forEach((code) => { account.tasks[code] = record('done', code); }));
    }
    if (kind === 'pending' && accounts[1]) {
      const code = codes.includes('arena') ? 'arena' : codes[0];
      accounts[1].tasks[code] = record('pending', code, {
        last_error: '已发送开战请求，但过渡结果未确认。该账号本轮已隔离，不重复开战；其他账号继续处理。',
        pending_actions: [`${code}_attempt_result_03`],
        evidence_paths: [`demo://evidence/${code}-pending.svg`],
        phase: '结果待核实',
      });
      const managedIndex = codes.indexOf('managed');
      const pendingIndex = codes.indexOf(code);
      codes.forEach((item, index) => {
        if (index > pendingIndex && index === managedIndex) accounts[1].tasks[item] = record('new', item);
      });
    }
    if (kind === 'offline' && accounts[2]) {
      accounts[2].health = '窗口连接中断';
      codes.forEach((code) => {
        accounts[2].tasks[code] = record('offline', code, { last_error: '账号掉线，本轮已跳过；掉线不计为完成。' });
      });
    }
    if (kind === 'not_open') {
      accounts.forEach((account) => codes.forEach((code) => {
        account.tasks[code] = code === 'managed'
          ? record('new', code)
          : record('skipped', code, { last_error: '执行日 2026-10-26：当前不在活动开放时间，未执行；不计完成。' });
      }));
    }
    const status = kind === 'not_open' ? 'not_open'
      : kind === 'pending' ? 'pending'
      : kind === 'complete' ? 'complete'
      : kind === 'stopped' ? 'stopped'
      : kind === 'stopping' ? 'stopping'
      // A disconnected account does not end the other accounts' run.
      : kind === 'offline' ? 'running'
      : kind === 'refresh_failed' ? 'running'
      : kind === 'fresh' ? 'running' : kind;
    let logs = clone(BASE_LOGS);
    if (kind === 'stopping' || kind === 'stopped') {
      logs.push({ time: '14:35:58', level: 'warning', account: '系统', event: 'unified_stop_requested', message: '停止已请求：正在结束本轮，保留断点，不启动新任务。' });
    }
    if (kind === 'stopped') {
      logs.push({ time: '14:36:01', level: 'warning', account: '系统', event: 'unified_finished', message: '本轮已停止；再次开始将按当前画面和选择创建新运行。' });
    }
    if (kind === 'pending') {
      logs.push({ time: '14:35:44', level: 'warning', account: '标签 2', event: 'combat_slot_quarantined_after_pending', message: '竞技场开战过渡结果未确认，该账号状态待核实，已继续处理其他账号。原动作：arena_attempt_result_03；证据：demo://evidence/arena-pending.svg；不会重复点击匹配或开战。' });
      logs.push({ time: '14:36:12', level: 'warning', account: '系统', event: 'unified_finished', message: '本轮仍有待处理项；未确认的任务、领奖或回村不会记为完成。' });
    }
    if (kind === 'offline') {
      logs.push({ time: '14:35:46', level: 'error', account: '标签 3', event: 'account_disconnected_skipped', message: '账号掉线，已跳过该账号；其他已核实账号继续，本轮不将掉线记作完成。' });
    }
    if (kind === 'not_open') {
      logs = [
        { time: '09:00:01', level: 'info', account: '系统', event: 'unified_run_started', message: '已创建独立周末任务演示运行。' },
        { time: '09:00:02', level: 'warning', account: '当前窗口', event: 'pipeline_task_not_open', message: '执行日 2026-10-26：当前不在开放时间，未执行；不计完成。' },
        { time: '09:00:03', level: 'warning', account: '系统', event: 'unified_finished', message: '所选活动当前未开放，没有可执行账号或任务；保留待处理原因。' },
      ];
    }
    if (kind === 'complete') {
      logs.push({ time: '14:36:18', level: 'success', account: '系统', event: 'unified_finished', message: '本轮所选任务、领奖、木叶回村与最后托管均已确认完成。' });
    }
    return {
      runId: `DEMO-${kind === 'not_open' ? 'CALENDAR' : '20261024-001'}`,
      day: kind === 'not_open' ? '2026-10-26' : '2026-10-24',
      startedAt: kind === 'not_open' ? '2026-10-26 09:00:01' : '2026-10-24 14:31:02',
      updatedAt: kind === 'not_open' ? '2026-10-26 09:00:03' : '2026-10-24 14:36:18',
      status,
      taskCodes: codes,
      plan,
      accounts,
      logs,
      warning: kind === 'refresh_failed' ? '进度索引读取失败，保留上次核实快照：模拟 SQLite 读取异常。' : '',
    };
  }

  function scenarioConfig(kind) {
    const config = freshConfig();
    if (kind !== 'idle' && kind !== 'not_open') {
      config.scope = 'multi';
      config.accountCount = 6;
    }
    if (kind === 'not_open') {
      config.section = 'independent';
      config.independent = 'weekend';
    }
    return config;
  }

  function currentTask(account, taskCodes) {
    const code = taskCodes.find((item) => ['running', 'pending', 'offline'].includes(account.tasks[item]?.status))
      || taskCodes.find((item) => account.tasks[item]?.status === 'new');
    return code ? taskLabel(code) : '—';
  }

  window.HYOL_DEMO = {
    SOLO_TASKS,
    TEAM_TASKS,
    PIPELINE_TASKS,
    INDEPENDENT_TASKS,
    ANNIVERSARY_TASKS,
    TASK_MAP,
    STATUS,
    RUN_STATUS,
    freshConfig,
    clone,
    taskLabel,
    selectedSoloTasks,
    selectedOrdered,
    defaultOrders,
    orderedCatalog,
    requestFor,
    validate,
    planFor,
    detailFor,
    record,
    makeSnapshot,
    scenarioConfig,
    currentTask,
  };
})();
