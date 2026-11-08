/* A presentation of engine records. This file does not execute Q1ASM. */
(() => {
  'use strict';
  const data = window.EXECUTION_FLOW_DATA;
  const byId = id => document.getElementById(id);
  const cases = data?.cases || [];
  const elements = Object.fromEntries([
    'program', 'scenario', 'case-description', 'evidence-status', 'input-label',
    'source-name', 'source-lines', 'previous-step', 'next-step',
    'previous-decision', 'next-decision', 'step-position', 'stage-title',
    'stage-subtitle', 'input-values', 'operation', 'output-values',
    'branch-paths', 'next-action', 'decision-ribbon', 'provenance',
    'run-end', 'return-focus', 'numeric-format', 'exploration',
    'exploration-label', 'reset-branches', 'branch-error', 'branch-hint',
    'history-point', 'history-more', 'history-table', 'history-empty',
    'view-title', 'view-caption', 'rt-relation', 'selection-context', 'instruction-zoom',
  ].map(id => [id, byId(id)]));
  let caseIndex = Math.max(0, cases.findIndex(item => item.id === 'latched'));
  let scenarioIndex = 0;
  let stepIndex = 0;
  let returnIndex = null;
  let eventIndexes = new Map();
  let decisionIndexes = [];
  const live = window.EXECUTION_FLOW_LIVE === true;
  let exploredScenario = null;
  let choices = [];
  let originalFocus = null;
  let busy = false;
  let branchError = '';
  let requestVersion = 0;
  let requestController = null;
  let historyLoop = null;
  let historyLimit = 5;
  let historyIteration = 1;
  let historyKey = null;
  let view = 'instruction';
  let focusedTime = false;
  let expandedLoops = new Set();
  let loopProjections = [];
  let detailTab = 'instruction';
  let historyDetail = false;
  let instructionWidth = 144;
  const operationNames = {
    move: '값 복사', add: '더하기', sub: '빼기',
    muls16: '부호 있는 16비트 곱셈', muls32: '부호 있는 32비트 곱셈',
    cmp: '값 비교', asr: '오른쪽 산술 시프트', fb_pop_data: '피드백 읽기',
    jge: '이상이면 이동', jl: '작으면 이동', jlt: '작으면 이동',
    jnz: '0이 아니면 이동', jz: '0이면 이동', jmp: '실행 위치 이동',
    loop: '반복 횟수 갱신', wait: 'RT 대기 명령', stop: '종료', nop: '다음 명령',
  };
  const currentCase = () => cases[caseIndex];
  const baselineScenario = () => currentCase()?.scenarios[scenarioIndex];
  const currentScenario = () => exploredScenario || baselineScenario();
  const steps = () => currentScenario()?.steps || [];
  const anchorFor = step => ({ pc: step.pc, occurrence: step.occurrence });
  const anchorIndex = (anchor, list = steps()) => anchor ? list.findIndex(step => step.pc === anchor.pc && step.occurrence === anchor.occurrence) : -1;
  const naturalTaken = branch => typeof branch.naturalTaken === 'boolean' ? branch.naturalTaken : branch.taken;
  function registerLabel(register) {
    const alias = Object.entries(currentCase()?.aliases || {}).find(([, value]) => value === register)?.[0];
    return alias ? `${register} · ${alias}` : register;
  }
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  function number(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '미확정';
    if (elements['numeric-format']?.value === 'raw') {
      return '0x' + (Number(value) >>> 0).toString(16).padStart(8, '0');
    }
    return Number(value).toLocaleString('en-US');
  }
  function time(value) {
    return value === null || value === undefined ? null : `${number(value)} ns`;
  }
  function setText(id, text) {
    if (elements[id]) elements[id].textContent = text;
  }
  function empty(container, text) {
    container.replaceChildren(el('div', 'empty-state', text));
  }
  function selectOptions(select, values) {
    select.replaceChildren(...values.map((item, index) => {
      const option = el('option', '', item.title);
      option.value = String(index);
      return option;
    }));
  }
  function selectStep(index, remember = false) {
    if (busy || !steps().length) return;
    if (remember && index !== stepIndex && returnIndex === null) returnIndex = stepIndex;
    stepIndex = Math.max(0, Math.min(steps().length - 1, index));
    if (detailTab === 'source') detailTab = 'instruction';
    if (detailTab === 'history') historyDetail = true;
    else document.querySelector('.panel-body').scrollTop = 0;
    loopProjections.forEach(item => {
      if (item.rows.some(row => stepIndex >= row.start && stepIndex <= row.end)) expandedLoops.add(item.loop.id);
    });
    render();
  }
  function jumpToWriter(writerId) {
    const index = eventIndexes.get(String(writerId));
    if (index !== undefined) selectStep(index, true);
  }
  function indexScenario() {
    const list = steps();
    eventIndexes = new Map(list.map((step, index) => [String(step.id), index]));
    decisionIndexes = list.flatMap((step, index) => step.branch && step.op !== 'jmp' ? [index] : []);
    loopProjections = (currentCase().loops || []).map(loop => ({ loop, ...projectLoop(list, loop) }));
  }
  function cancelRequest() {
    requestVersion += 1;
    requestController?.abort();
    requestController = null;
    busy = false;
  }
  function loadScenario() {
    cancelRequest();
    exploredScenario = null;
    choices = [];
    originalFocus = null;
    branchError = '';
    indexScenario();
    const firstLoop = currentCase().loops?.[0];
    const firstSetter = steps().findIndex(step => step.latched?.length && firstLoop && step.pc >= firstLoop.startPc && step.pc <= firstLoop.endPc);
    stepIndex = decisionIndexes.find(index => steps()[index].op !== 'loop') ?? (firstSetter >= 0 ? firstSetter : decisionIndexes[0] ?? 0);
    historyLoop = currentCase().loops?.[0]?.id ?? null;
    historyLimit = 5;
    historyIteration = 1;
    historyKey = null;
    detailTab = 'instruction';
    historyDetail = false;
    document.querySelector('.panel-body').scrollTop = 0;
    expandedLoops = new Set(currentCase().loops?.length ? [currentCase().loops[0].id] : []);
    returnIndex = null;
    renderSource();
    render();
  }
  function resetBranches() {
    const anchor = originalFocus || (steps()[stepIndex] && anchorFor(steps()[stepIndex]));
    cancelRequest();
    exploredScenario = null;
    choices = [];
    originalFocus = null;
    branchError = '';
    returnIndex = null;
    indexScenario();
    stepIndex = Math.max(0, anchorIndex(anchor));
    renderSource();
    render();
    elements['branch-paths'].querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
  }
  async function chooseBranch(taken) {
    const step = steps()[stepIndex];
    if (!live || busy || !step?.branch || step.branch.taken === taken) return;
    const anchor = anchorFor(step);
    const nextChoices = choices.filter(choice => {
      const index = anchorIndex(choice);
      return index >= 0 && index < stepIndex;
    });
    if (taken !== naturalTaken(step.branch)) nextChoices.push({ ...anchor, taken });
    const firstFocus = originalFocus || anchor;
    const controller = new AbortController();
    requestController = controller;
    const version = ++requestVersion;
    busy = true;
    branchError = '';
    render();
    try {
      const response = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caseId: currentCase().id, scenarioId: baselineScenario().id, choices: nextChoices }),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || result.message || `실행 요청 실패 (${response.status})`);
      if (version !== requestVersion) return;
      exploredScenario = result;
      choices = result.choices || nextChoices;
      originalFocus = choices.length ? firstFocus : null;
      returnIndex = null;
      indexScenario();
      stepIndex = Math.max(0, anchorIndex(anchor));
      renderSource();
    } catch (error) {
      if (version !== requestVersion || error.name === 'AbortError') return;
      branchError = error.message || '경로를 계산하지 못했습니다.';
    } finally {
      if (version === requestVersion) {
        busy = false;
        requestController = null;
        render();
        elements['branch-paths'].querySelector(`[data-taken="${taken}"]`)?.focus({ preventScroll: true });
      }
    }
  }
  function renderSource() {
    const list = steps();
    const executedLines = new Set(list.map(step => step.line));
    elements['source-lines'].replaceChildren(...currentCase().source.lines.flatMap((text, index) => {
      const line = index + 1;
      const row = el(executedLines.has(line) ? 'button' : 'div', 'source-line');
      row.dataset.line = String(line);
      if (row.tagName === 'BUTTON') {
        row.type = 'button';
        row.disabled = busy;
        row.setAttribute('aria-label', `소스 ${line}행의 가까운 실행 보기`);
        row.addEventListener('click', () => {
          let nearest = null;
          list.forEach((step, candidate) => {
            if (step.line === line && (nearest === null || Math.abs(candidate - stepIndex) < Math.abs(nearest - stepIndex))) nearest = candidate;
          });
          if (nearest !== null) {
            const loop = currentCase().loops?.find(item => item.endPcs.includes(list[nearest].pc));
            if (loop) selectLoop(loop.id);
            else { historyKey = null; selectStep(nearest, true); }
          }
        });
      }
      row.append(el('span', 'source-number', line), el('code', 'source-code', text || ' '));
      const loop = currentCase().loops?.find(item => item.startLine === line);
      if (!loop) return [row];
      const marker = el('button', 'loop-marker', `↻ @${loop.name} · L${loop.startLine}–${loop.endLine}`);
      marker.type = 'button';
      marker.dataset.loop = loop.id;
      marker.setAttribute('aria-label', `루프 ${loop.name}의 처음 5회 보기`);
      marker.addEventListener('click', () => selectLoop(loop.id));
      return [marker, row];
    }));
    setText('source-name', currentCase().source.path.split(/[\\/]/).pop());
    elements['source-name'].title = currentCase().source.path;
  }
  function showSource(step) {
    const evidenceLines = new Set(inputsFor(step).map(read => {
      const index = eventIndexes.get(String(read.writerId));
      return index === undefined ? null : steps()[index].line;
    }));
    const producerIndex = eventIndexes.get(String(step.branch?.producerId));
    if (producerIndex !== undefined) evidenceLines.add(steps()[producerIndex].line);
    let selected;
    elements['source-lines'].querySelectorAll('.source-line').forEach(row => {
      const line = Number(row.dataset.line);
      row.classList.toggle('is-active', line === step.line);
      row.classList.toggle('is-next', line === step.nextLine && line !== step.line);
      row.classList.toggle('is-evidence', evidenceLines.has(line) && line !== step.line);
      if (line === step.line) selected = row;
    });
    if (selected) {
      const host = elements['source-lines'];
      const rowTop = selected.offsetTop;
      if (rowTop < host.scrollTop + 25 || rowTop + selected.offsetHeight > host.scrollTop + host.clientHeight - 25) {
        host.scrollTop = Math.max(0, rowTop - host.clientHeight * 0.4);
      }
    }
  }
  function inputsFor(step) {
    return step.branch?.operands?.length ? step.branch.operands : step.reads || [];
  }
  function inputCard(label, value, detail, writerId) {
    const canJump = writerId !== null && writerId !== undefined && eventIndexes.has(String(writerId));
    const card = el(canJump ? 'button' : 'div', 'value-card');
    card.append(el('span', 'value-name', label), el('strong', 'value-number', number(value)), el('span', 'value-meta', detail));
    if (canJump) {
      card.type = 'button';
      card.disabled = busy;
      card.classList.add('provenance-jump');
      card.setAttribute('aria-label', `${label} = ${number(value)}, 값을 쓴 명령으로 이동`);
      card.addEventListener('click', () => jumpToWriter(writerId));
    }
    return card;
  }
  function renderInputs(step) {
    const cards = inputsFor(step).map(read => {
      const writerIndex = eventIndexes.get(String(read.writerId));
      const writer = writerIndex === undefined ? null : steps()[writerIndex];
      const detail = writer ? `↖ L${writer.line} · ${writer.occurrence}회차에서 기록` : read.register ? '레지스터 입력' : '상수';
      return inputCard(read.register ? registerLabel(read.register) : read.name || read.raw || '상수', read.value, detail, read.writerId);
    });
    (step.branch?.operands?.length ? [] : step.resolvedArgs || []).filter(arg => {
      const token = String(arg.raw).replace(/^\$/, '');
      return arg.kind !== 'label' && !/^R\d+$/.test(token) && !currentCase().aliases?.[token];
    }).forEach(arg => {
      cards.push(inputCard(String(arg.raw ?? arg.index + 1), arg.value, '상수', null));
    });
    if (step.input) cards.push(inputCard(step.input.label || '시나리오 입력', step.input.value, '가상 입력 · 실측 아님', null));
    if (cards.length) elements['input-values'].replaceChildren(...cards);
    else empty(elements['input-values'], '레지스터 읽기 없음');
  }
  function renderOutputs(step) {
    const cards = (step.writes || []).map(write => {
      const card = el('div', 'value-card output-card');
      card.append(el('span', 'value-name', registerLabel(write.register)));
      const change = el('div', 'value-change');
      change.append(el('span', 'before-value', number(write.before)), el('span', 'change-arrow', '→'), el('strong', 'value-number', number(write.after)));
      card.append(change, el('span', 'value-meta', write.readyAt !== null && write.readyAt !== undefined ? `${time(write.readyAt)}부터 읽을 수 있음` : '엔진이 기록한 쓰기'));
      return card;
    });
    (step.pendingWrites || []).forEach(write => {
      const card = el('div', 'value-card pending-output');
      card.append(el('span', 'value-name', `${registerLabel(write.register)} · 예약된 값`), el('strong', 'value-number', number(write.value)), el('span', 'value-meta', '쓰기 대기 · 중단 시점에는 아직 읽을 수 없음'));
      cards.push(card);
    });
    (step.latched || []).forEach(write => {
      const card = el('div', 'value-card output-card');
      card.append(el('span', 'value-name', `Latched · ${write.field}`), el('strong', 'value-number', displayValue(write.after)),
        el('span', 'value-meta', '설정값 갱신 · RT 적용 시점과 구분'));
      cards.push(card);
    });
    if (step.branch) {
      const branch = step.branch;
      const card = el('div', `value-card output-card${branch.overridden ? ' hypothetical-output' : ''}`);
      const natural = naturalTaken(branch);
      const known = typeof natural === 'boolean';
      const unconditional = step.op === 'jmp';
      const answer = known ? (natural ? '예' : '아니오') : '미확정';
      const path = typeof branch.taken === 'boolean' ? (branch.taken ? 'Jump' : 'Continue') : '경로 미확정';
      const detail = branch.overridden ? `조건은 ${answer} · ${path} 가정` : path;
      card.append(el('span', 'value-name', unconditional ? '다음 실행' : '조건 충족'), el('strong', 'value-number', unconditional ? 'Jump' : answer), el('span', 'value-meta', `${detail}${step.nextLine ? ` · 다음 L${step.nextLine}` : ' · 다음 경로 기록 없음'}`));
      cards.push(card);
    }
    if (cards.length) elements['output-values'].replaceChildren(...cards);
    else empty(elements['output-values'], step.op === 'stop' ? '프로그램 종료 명령' : '레지스터 쓰기 없음');
  }
  function renderBranch(step) {
    const host = elements['branch-paths'];
    host.hidden = !step.branch || step.op === 'jmp';
    if (elements['branch-hint']) {
      elements['branch-hint'].hidden = host.hidden;
      setText('branch-hint', live ? '이 분기의 입력값을 유지하고, 선택한 경로의 이후 실행을 다시 계산합니다.' : '정적 기록입니다. 로컬 실행 서버에서 다른 경로를 탐색할 수 있습니다.');
    }
    if (host.hidden) { host.replaceChildren(); return; }
    host.replaceChildren(...[true, false].map(taken => {
      const chosen = step.branch.taken === taken;
      const natural = naturalTaken(step.branch) === taken;
      const route = el('button', `route${chosen ? ' is-taken' : ''}${chosen && step.branch.overridden ? ' is-overridden' : ''}`);
      route.type = 'button';
      route.dataset.taken = String(taken);
      route.disabled = busy || !live;
      // Keep the selected row focusable after replay while making it inoperable.
      route.setAttribute('aria-disabled', String(chosen || busy || !live));
      route.setAttribute('aria-pressed', String(chosen));
      const line = taken ? step.branch.targetLine : step.branch.fallthroughLine;
      const state = chosen ? (step.branch.overridden ? '가정 경로' : '현재 경로') : live ? '이 경로로 실행 →' : '실행하지 않음';
      const detail = `${natural ? '조건에 따른 경로 · ' : ''}${state}`;
      route.append(el('span', 'route-name', taken ? 'Jump' : 'Continue'), el('span', 'route-condition', detail), el('span', 'route-target', line ? `L${line}` : '주소 미확정'));
      route.addEventListener('click', () => { if (!chosen) chooseBranch(taken); });
      return route;
    }));
  }
  function renderProvenance(step) {
    const items = inputsFor(step).filter(read => read.register).map(read => {
      const item = el('div', 'provenance-item');
      item.append(el('span', 'value-name', `${registerLabel(read.register)} = ${number(read.value)}`));
      const writerIndex = eventIndexes.get(String(read.writerId));
      if (writerIndex !== undefined) {
        const writer = steps()[writerIndex];
        const button = el('button', 'provenance-jump', `L${writer.line} · ${writer.occurrence}회 · ${writer.text}`);
        button.type = 'button';
        button.disabled = busy;
        button.addEventListener('click', () => jumpToWriter(read.writerId));
        item.append(button);
      } else item.append(el('span', 'value-meta', '이 기록에서 작성 명령을 연결할 수 없음'));
      return item;
    });
    const producerIndex = eventIndexes.get(String(step.branch?.producerId));
    if (producerIndex !== undefined && producerIndex !== stepIndex) {
      const producer = steps()[producerIndex];
      const item = el('div', 'provenance-item');
      const button = el('button', 'provenance-jump', `L${producer.line} · ${producer.occurrence}회 · ${producer.text}`);
      button.type = 'button';
      button.disabled = busy;
      button.addEventListener('click', () => jumpToWriter(producer.id));
      item.append(el('span', 'value-name', '판단에 사용된 비교'), button);
      items.unshift(item);
    }
    if (step.input) {
      const item = el('div', 'provenance-item external-input');
      item.append(el('span', 'value-name', step.input.label || '시나리오 입력'), el('strong', 'value-number', number(step.input.value)), el('span', 'value-meta', `명시적으로 제공한 가상 입력${step.input.timeNs != null ? ` · ${time(step.input.timeNs)}` : ''}`));
      items.push(item);
    }
    if (items.length) elements['provenance'].replaceChildren(...items);
    else empty(elements['provenance'], '레지스터 입력이 없는 명령입니다. 상수와 설정은 원문에서 확인할 수 있습니다.');
  }
  function renderRibbon() {
    const occurrences = new Map();
    const chips = decisionIndexes.map(index => {
      const step = steps()[index];
      const occurrence = (occurrences.get(step.line) || 0) + 1;
      occurrences.set(step.line, occurrence);
      const button = el('button', `decision-chip${index === stepIndex ? ' is-active' : ''}${step.branch.overridden ? ' is-overridden' : ''}`);
      button.type = 'button';
      button.disabled = busy;
      button.setAttribute('aria-pressed', String(index === stepIndex));
      button.setAttribute('aria-label', `L${step.line}의 ${occurrence}번째 판단, ${step.branch.taken === true ? 'Jump' : step.branch.taken === false ? 'Continue' : '미확정'}${step.branch.overridden ? ' 가정' : ''}`);
      button.append(el('span', 'decision-label', `L${step.line} · ${occurrence}회`), el('strong', 'decision-result', `${step.branch.taken === true ? 'Jump' : step.branch.taken === false ? 'Continue' : '?'}${step.branch.overridden ? ' · 가정' : ''}`));
      button.addEventListener('click', () => selectStep(index));
      return button;
    });
    if (chips.length) elements['decision-ribbon'].replaceChildren(...chips);
    else empty(elements['decision-ribbon'], '이 실행에는 기록된 조건 판단이 없습니다.');
  }
  function displayValue(value) {
    if (Array.isArray(value)) return `[${value.map(displayValue).join(', ')}]`;
    return value == null || typeof value === 'object' ? '—' : number(value);
  }
  function selectLoop(id) {
    if (busy) return;
    historyLoop = id;
    expandedLoops.add(id);
    historyLimit = 5;
    historyIteration = 1;
    historyKey = null;
    detailTab = 'history';
    historyDetail = false;
    document.querySelector('.panel-body').scrollTop = 0;
    const loop = currentCase().loops.find(item => item.id === id);
    const first = projectLoop(steps(), loop).rows[0];
    if (first) stepIndex = first.focusIndex;
    render();
  }
  function renderHistory() {
    const loop = currentCase().loops?.find(item => item.id === historyLoop);
    const { columns, rows } = projectLoop(steps(), loop);
    setText('history-point', loop ? `@${loop.name} · 처음 ${Math.min(historyLimit, rows.length)} / ${rows.length}회` : '소스에서 루프를 선택하세요');
    elements['history-more'].hidden = historyLimit >= rows.length;
    elements['history-more'].disabled = busy;
    elements['history-table'].hidden = !rows.length;
    elements['history-empty'].hidden = Boolean(rows.length);
    setText('history-empty', loop ? '이 경로에는 실행된 회차가 없습니다.' : '뒤로 이동하는 분기를 가진 루프를 선택하면 처음 5회가 자동으로 열립니다.');
    elements['source-lines'].querySelectorAll('.loop-marker').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.loop === historyLoop));
    });
    if (!rows.length) return;
    if (!historyKey) {
      const current = rows.find(row => stepIndex >= row.start && stepIndex <= row.end);
      if (current) historyIteration = current.iteration;
    }
    const head = el('thead');
    const headings = el('tr');
    ['회차', ...columns.map(column => column.kind === 'register' ? registerLabel(column.key) : `Latched · ${column.key}`), '흐름'].forEach(label => {
      const cell = el('th', '', label);
      cell.scope = 'col';
      headings.append(cell);
    });
    head.append(headings);
    const body = el('tbody');
    rows.slice(0, historyLimit).forEach(row => {
      const selected = row.iteration === historyIteration;
      const tr = el('tr', `${selected ? 'is-selected' : ''}${row.assumed ? ' is-overridden' : ''}`);
      const label = el('th');
      label.scope = 'row';
      const visit = el('button', 'history-visit', `${row.iteration}회`);
      visit.type = 'button';
      visit.disabled = busy;
      visit.setAttribute('aria-label', `루프 ${loop.name}의 ${row.iteration}회차 보기`);
      visit.addEventListener('click', () => { historyKey = null; selectStep(row.focusIndex); });
      label.append(visit);
      tr.append(label);
      columns.forEach(column => {
        const cell = row.cells[column.id];
        const td = el('td', `${cell?.changes.length ? 'is-changed' : ''}${selected && column.id === historyKey ? ' is-focused' : ''}`);
        if (cell?.changes.length) td.append(el('span', 'history-before', displayValue(cell.changes[0].before)));
        const values = cell?.changes.length ? cell.changes : cell?.held ? [cell.held] : [];
        values.forEach(change => {
          if (cell.changes.length) td.append(el('span', 'history-arrow', ' → '));
          const writer = steps()[change.index];
          const button = el('button', 'history-value', displayValue(change.after));
          button.type = 'button';
          button.disabled = busy;
          button.title = `L${writer.line} · ${writer.occurrence}회 · ${writer.text}`;
          button.setAttribute('aria-label', `${loop.name} ${row.iteration}회차 ${column.key} = ${displayValue(change.after)}. L${writer.line} 출처 보기`);
          button.addEventListener('click', () => {
            historyIteration = row.iteration;
            historyKey = column.id;
            selectStep(change.index, true);
          });
          td.append(button);
        });
        if (!values.length) td.textContent = '—';
        tr.append(td);
      });
      const route = el('td');
      const button = el('button', 'history-route', `${row.status}${row.assumed ? ' · 가정' : ''}`);
      button.type = 'button';
      button.disabled = busy;
      button.addEventListener('click', () => { historyKey = null; selectStep(row.end); });
      route.append(button);
      tr.append(route);
      body.append(tr);
    });
    elements['history-table'].replaceChildren(head, body);
  }
  function commandBlock(op, text) {
    const button = el('button', 'timeline-block has-command-color');
    button.type = 'button';
    button.disabled = busy;
    button.style.setProperty('--timeline-block-accent', q1asmInstructionColors.commandAccentColor(op));
    const label = el('span', 'block-label');
    const source = text.replace(/\s+/g, ' ').trim();
    let offset = 0;
    q1asmInstructionColors.sourceTokens(source).forEach(token => {
      label.append(document.createTextNode(source.slice(offset, token.start)));
      const span = el('span', '', source.slice(token.start, token.end));
      span.style.color = token.kind === 'command' ? q1asmInstructionColors.commandAccentColor(op) : q1asmInstructionColors.argumentAccentColor(token.kind);
      label.append(span);
      offset = token.end;
    });
    label.append(document.createTextNode(source.slice(offset)));
    button.append(label);
    return button;
  }
  function instructionNode(index) {
    const step = steps()[index];
    const button = commandBlock(step.op, step.text);
    button.classList.add('q1-node');
    button.dataset.index = index;
    button.setAttribute('aria-label', `Q1 L${step.line} ${step.occurrence}회 ${step.op}`);
    button.setAttribute('aria-selected', String(index === stepIndex));
    button.title = `#${index + 1} · L${step.line} · ${step.occurrence}번째 실행\n${step.text}`;
    button.addEventListener('click', () => { detailTab = 'instruction'; historyKey = null; selectStep(index); });
    return button;
  }
  function timelineRow(label, width, className = '') {
    const row = el('div', `timeline-row ${className}`);
    const track = el('div', 'lane-track');
    track.style.width = `${width}px`;
    row.append(el('div', 'lane-label', label), track);
    return { row, track };
  }
  function renderInstruction() {
    const host = byId('instruction-view');
    const layout = ExecutionViews.instructionLayout(steps(), loopProjections, expandedLoops, stepIndex, historyLoop, historyIteration);
    const width = Math.max(host.clientWidth - 168, layout.blocks.length * instructionWidth + 8);
    const canvas = el('div', 'timeline-canvas');
    canvas.style.width = `${width + 168}px`;
    const ruler = el('div', 'ruler');
    const stride = Math.max(1, Math.ceil(60 / instructionWidth));
    layout.blocks.forEach((block, position) => {
      if (position % stride) return;
      const tick = el('div', 'tick');
      tick.style.left = `${position * instructionWidth}px`;
      tick.append(el('span', '', block.kind === 'instruction' ? `#${block.index + 1}` : '↻'));
      ruler.append(tick);
    });
    canvas.append(el('div', 'axis-label', '실행 순서 #'), ruler);
    const depth = layout.ranges.length ? Math.max(...layout.ranges.map(range => range.depth)) + 1 : 0;
    for (let level = 0; level < depth; level++) {
      const band = timelineRow(level ? '중첩 반복' : '반복 범위', width, 'loop-band-row');
      layout.ranges.filter(range => range.depth === level).forEach(range => {
        const strip = el('div', 'loop-band');
        strip.style.left = `${range.start * instructionWidth}px`;
        strip.style.width = `${(range.end - range.start) * instructionWidth - 4}px`;
        strip.classList.toggle('is-active', range.loop.id === historyLoop && detailTab === 'history');
        const toggle = el('button', 'loop-toggle', range.expanded ? '⌄' : '›');
        toggle.type = 'button';
        toggle.disabled = busy;
        toggle.setAttribute('aria-label', `루프 ${range.loop.name} ${range.expanded ? '접기' : '펼치기'}`);
        toggle.setAttribute('aria-expanded', String(range.expanded));
        toggle.addEventListener('click', () => {
          if (range.expanded) { expandedLoops.delete(range.loop.id); render(); }
          else selectLoop(range.loop.id);
        });
        const title = `↻ @${range.loop.name} · ${range.row.iteration} / ${range.rows.length}회`;
        const button = el('button', 'loop-title', title);
        button.type = 'button';
        button.disabled = busy;
        button.title = `${title}\nL${range.loop.startLine}–${range.loop.endLine} · 클릭하여 처음 5회 변화 보기`;
        button.setAttribute('aria-label', `루프 ${range.loop.name}의 처음 5회 보기`);
        button.addEventListener('click', () => selectLoop(range.loop.id));
        strip.append(toggle, button);
        band.track.append(strip);
      });
      canvas.append(band.row);
    }
    const lane = timelineRow('Q1 · 명령', width);
    layout.blocks.forEach((block, position) => {
      const button = block.kind === 'instruction' ? instructionNode(block.index) : commandBlock('loop', `↻ @${block.loop.name}`);
      button.style.left = `${position * instructionWidth}px`;
      button.style.width = `${instructionWidth - 4}px`;
      if (block.kind === 'loop') {
        button.title = `${block.rows.length}회 기록 · 클릭하여 펼치기`;
        button.addEventListener('click', () => selectLoop(block.loop.id));
      }
      lane.track.append(button);
    });
    canvas.append(lane.row);
    host.replaceChildren(canvas);
    if (view === 'instruction') {
      const selected = host.querySelector('.q1-node[aria-selected="true"]');
      if (selected) {
        const box = selected.getBoundingClientRect(), frame = host.getBoundingClientRect();
        if (box.left < frame.left + 168 || box.right > frame.right) host.scrollLeft += box.left - frame.left - 192;
      }
    }
    setText('instruction-zoom', `${Math.round(instructionWidth / 144 * 100)}%`);
  }
  function renderTime() {
    const selected = steps()[stepIndex];
    if (!selected) return;
    const host = byId('time-view');
    const packets = currentScenario().rtPackets || [];
    const related = new Set(ExecutionViews.relatedPackets(selected, packets).map(packet => packet.id));
    const range = ExecutionViews.timeWindow(steps(), packets, selected, focusedTime);
    const width = Math.max(520, host.clientWidth - 180);
    const x = value => (value - range.start) / (range.end - range.start) * width;
    const canvas = el('div', 'timeline-canvas');
    canvas.style.width = `${width + 168}px`;
    const ruler = el('div', 'ruler');
    const unit = range.end >= 1000 ? 1000 : 1;
    for (let i = 0; i < 6; i++) {
      const value = range.start + (range.end - range.start) * i / 6;
      const tick = el('div', 'tick');
      tick.style.left = `${x(value)}px`;
      tick.append(el('span', '', `${Number((value / unit).toFixed(1))} ${unit === 1000 ? 'µs' : 'ns'}`));
      ruler.append(tick);
    }
    canvas.append(el('div', 'axis-label', '실행 시간'), ruler);
    const q1 = timelineRow('Q1 · 명령', width), rt = timelineRow('RT · 동작', width);
    const place = (track, button, start, end) => {
      if (!Number.isFinite(start) || !Number.isFinite(end) || end < range.start || start > range.end) return;
      const left = x(Math.max(range.start, start));
      const blockWidth = Math.max(1, x(Math.min(range.end, end)) - left);
      button.style.left = `${left}px`;
      button.style.width = `${blockWidth}px`;
      if (blockWidth < 25) button.classList.add('is-compact');
      track.append(button);
    };
    steps().forEach((step, index) => {
      const button = instructionNode(index);
      button.title += `\nQ1 ${time(step.q1Start)} → ${time(step.q1End)}`;
      place(q1.track, button, step.q1Start, step.q1End);
    });
    packets.forEach(packet => {
      const button = commandBlock(packet.op, packet.op);
      button.setAttribute('aria-selected', String(related.has(packet.id)));
      button.setAttribute('aria-label', `RT ${packet.op} L${packet.line} ${time(packet.start)}부터`);
      button.title = `RT ${packet.op} · L${packet.line}\n${time(packet.start)} → ${time(packet.end)}`;
      button.addEventListener('click', () => {
        const index = eventIndexes.get(String(packet.q1Id));
        if (index !== undefined) { detailTab = 'instruction'; selectStep(index); }
      });
      place(rt.track, button, packet.start, packet.end);
    });
    canvas.append(q1.row, rt.row);
    const links = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    links.setAttribute('class', 'time-links');
    links.setAttribute('viewBox', `0 0 ${width} 72`);
    links.setAttribute('aria-hidden', 'true');
    links.style.width = `${width}px`;
    packets.filter(packet => related.has(packet.id) && Number.isFinite(packet.start)).forEach(packet => {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M${x(selected.q1End)},29 L${x(packet.start)},43`);
      links.append(path);
    });
    canvas.append(links);
    const unknown = packets.filter(packet => !Number.isFinite(packet.start)).length;
    host.replaceChildren(canvas, el('p', 'time-note', `${focusedTime ? '선택 명령과 연결된 RT 주변' : '전체 실행'} · 작은 명령은 가는 선으로 표시${unknown ? ` · ${unknown}개 packet의 RT 실행 시각 미확정` : ''}`));
    byId('time-focus').setAttribute('aria-pressed', String(focusedTime));
    byId('time-all').setAttribute('aria-pressed', String(!focusedTime));
  }
  function renderRtContext() {
    const step = steps()[stepIndex];
    if (!step) return;
    const packets = ExecutionViews.relatedPackets(step, currentScenario().rtPackets || []);
    setText('rt-relation', packets.length ? `${step.latched?.length ? '설정값이 적용되는 RT' : '이 명령이 만든 packet'} · ${packets.length}개` : 'Q1 연산 · 직접 연결된 RT 기록 없음');
    const links = packets.slice(0, 3).map(packet => {
      const button = el('button', 'rt-link');
      button.type = 'button';
      button.disabled = busy;
      const fields = step.latched?.length ? step.latched.map(item => item.field) : packet.updatedFields;
      button.append(el('strong', '', `${packet.op} · L${packet.line}`), el('span', '', Number.isFinite(packet.start) ? `${time(packet.start)} → ${time(packet.end)}` : 'RT 실행 미기록'));
      const settings = fields.filter(field => packet.settings[field] !== undefined).map(field => `${field} ${displayValue(packet.settings[field])}`).join(' · ');
      if (settings) button.append(el('code', '', settings));
      button.title = 'Time 보기에서 이 명령과 RT 관계 확인';
      button.addEventListener('click', () => { view = 'time'; focusedTime = true; render(); });
      return button;
    });
    byId('rt-links').replaceChildren(...links);
  }
  function renderViews() {
    byId('instruction-view').hidden = view !== 'instruction';
    byId('time-view').hidden = view !== 'time';
    byId('time-controls').hidden = view !== 'time';
    byId('instruction-controls').hidden = view !== 'instruction';
    byId('instruction-view-button').setAttribute('aria-pressed', String(view === 'instruction'));
    byId('time-view-button').setAttribute('aria-pressed', String(view === 'time'));
    setText('view-title', view === 'instruction' ? 'Q1 · 실행 순서' : 'Q1 / RT · 실제 시간');
    setText('view-caption', view === 'instruction' ? '실행 번호 # · 같은 폭의 명령 · 반복은 선택한 한 회차' : '같은 시간축 · 점선은 Q1과 RT 연결');
    renderDock();
    renderInstruction();
    if (view === 'time') renderTime();
    renderRtContext();
  }
  function renderDock() {
    ['instruction', 'history', 'source'].forEach(tab => {
      const button = byId(`detail-${tab}`);
      button.setAttribute('aria-selected', String(detailTab === tab));
      button.tabIndex = detailTab === tab ? 0 : -1;
    });
    byId('loop-detail').hidden = detailTab !== 'history';
    byId('selection-detail').hidden = detailTab === 'source' || (detailTab === 'history' && !historyDetail);
    byId('source-detail').hidden = detailTab !== 'source';
    const step = steps()[stepIndex];
    setText('selection-context', step ? `#${stepIndex + 1} · L${step.line} · ${step.occurrence}번째 실행` : '');
    if (detailTab === 'source') {
      document.querySelector('.source-reference').open = true;
      if (step) showSource(step);
    }
  }
  function render() {
    const scenario = currentScenario();
    const list = steps();
    const step = list[stepIndex];
    setText('case-description', currentCase().description || '');
    elements['case-description'].title = currentCase().description || '';
    const startup = scenario.scenario?.startup_delay_ns;
    setText('input-label', `${scenario.inputLabel || '외부 입력 없음'}${startup != null ? ` · RT 시작 지연 가정 ${time(startup)}` : ''}`);
    const complete = scenario.status === 'completed' || scenario.status === 'complete';
    setText('evidence-status', `${choices.length ? '가정 경로 · ' : ''}${complete ? '실행 완료' : '부분 실행'}`);
    elements['evidence-status'].dataset.status = choices.length ? 'hypothetical' : complete ? 'complete' : 'partial';
    const pending = scenario.pending;
    const reason = scenario.reason || pending?.reason || '';
    setText('run-end', complete ? `기록된 Q1 명령 ${list.length}개 · 설정된 시나리오의 계산 결과` : `실행 한계${pending?.line || scenario.statusLine ? ` · L${pending?.line || scenario.statusLine}` : ''}: ${reason || scenario.status}`);
    elements['run-end'].dataset.status = complete ? 'complete' : 'partial';
    elements['run-end'].hidden = complete;
    elements['previous-step'].disabled = busy || !step || stepIndex === 0;
    elements['next-step'].disabled = busy || !step || stepIndex >= list.length - 1;
    elements['previous-decision'].disabled = busy || !decisionIndexes.some(index => index < stepIndex);
    elements['next-decision'].disabled = busy || !decisionIndexes.some(index => index > stepIndex);
    elements['source-lines'].querySelectorAll('button').forEach(button => { button.disabled = busy; });
    if (elements['return-focus']) {
      elements['return-focus'].hidden = returnIndex === null;
      elements['return-focus'].disabled = busy;
    }
    if (elements.exploration) elements.exploration.hidden = !choices.length && !busy;
    setText('exploration-label', busy ? '가정 경로 계산 중…' : `가정 경로 · ${choices.length}곳 변경`);
    if (elements['reset-branches']) elements['reset-branches'].disabled = !choices.length && !busy;
    if (elements['branch-error']) {
      elements['branch-error'].hidden = !branchError;
      setText('branch-error', branchError ? `경로 계산 실패: ${branchError} · 이전 실행을 유지합니다.` : '');
    }
    elements['branch-paths'].setAttribute('aria-busy', String(busy));
    setText('step-position', step ? `${stepIndex + 1} / ${list.length}` : '기록 없음');
    if (!step) {
      renderHistory();
      setText('stage-title', '실행 기록을 만들 수 없습니다');
      setText('stage-subtitle', reason);
      ['input-values', 'operation', 'output-values', 'provenance', 'decision-ribbon', 'branch-paths'].forEach(id => elements[id].replaceChildren());
      if (elements['branch-hint']) elements['branch-hint'].hidden = true;
      setText('next-action', '');
      return;
    }
    const visits = list.slice(0, stepIndex + 1).filter(item => item.line === step.line).length;
    setText('stage-title', `L${step.line} · ${step.op}`);
    const q1 = step.q1Start != null && step.q1End != null ? ` · Q1 ${time(step.q1Start)} → ${time(step.q1End)}` : '';
    setText('stage-subtitle', `${visits}번째 실행${q1}`);
    elements['operation'].replaceChildren(el('span', 'operation-name', operationNames[step.op] || step.op), el('code', 'operation-source', step.text));
    if (step.branch?.condition) elements['operation'].append(el('strong', 'operation-condition', step.op === 'jmp' ? step.branch.condition : `${step.branch.condition} ?`));
    renderInputs(step);
    renderOutputs(step);
    renderBranch(step);
    renderProvenance(step);
    renderRibbon();
    showSource(step);
    renderHistory();
    renderViews();
    const nextStep = list[stepIndex + 1];
    if (nextStep) {
      const next = el('button', 'next-instruction', `다음 명령 L${nextStep.line} · ${nextStep.op} →`);
      next.type = 'button';
      next.disabled = busy;
      next.addEventListener('click', () => selectStep(stepIndex + 1));
      elements['next-action'].replaceChildren(next);
    } else setText('next-action', step.op === 'stop' ? 'stop 명령' : '다음 실행 기록 없음');
  }
  if (!cases.length) {
    setText('evidence-status', '실행 데이터가 없습니다. build.py로 데모를 생성해주세요.');
    return;
  }
  selectOptions(elements.program, cases);
  elements.program.value = String(caseIndex);
  function loadCase() {
    selectOptions(elements.scenario, currentCase().scenarios);
    scenarioIndex = 0;
    loadScenario();
  }
  elements.program.addEventListener('change', () => { caseIndex = Number(elements.program.value); loadCase(); });
  elements.scenario.addEventListener('change', () => { scenarioIndex = Number(elements.scenario.value); loadScenario(); });
  elements['previous-step'].addEventListener('click', () => selectStep(stepIndex - 1));
  elements['next-step'].addEventListener('click', () => selectStep(stepIndex + 1));
  elements['previous-decision'].addEventListener('click', () => {
    const index = [...decisionIndexes].reverse().find(item => item < stepIndex);
    if (index !== undefined) selectStep(index);
  });
  elements['next-decision'].addEventListener('click', () => {
    const index = decisionIndexes.find(item => item > stepIndex);
    if (index !== undefined) selectStep(index);
  });
  elements['return-focus']?.addEventListener('click', () => {
    if (busy) return;
    const index = returnIndex;
    returnIndex = null;
    if (index !== null) selectStep(index);
  });
  elements['numeric-format']?.addEventListener('change', render);
  elements['reset-branches']?.addEventListener('click', resetBranches);
  elements['history-more'].addEventListener('click', () => { historyLimit += 5; renderHistory(); });
  document.addEventListener('keydown', event => {
    if (event.target.closest('input, select, textarea, button, [contenteditable]') || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      selectStep(stepIndex + (event.key === 'ArrowLeft' ? -1 : 1));
    }
  });
  ['instruction', 'time'].forEach(mode => byId(`${mode}-view-button`).addEventListener('click', () => { view = mode; renderViews(); }));
  ['instruction', 'history', 'source'].forEach((tab, index, tabs) => {
    const button = byId(`detail-${tab}`);
    button.addEventListener('click', () => { detailTab = tab; document.querySelector('.panel-body').scrollTop = 0; render(); });
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      detailTab = next;
      render();
      byId(`detail-${next}`).focus();
    });
  });
  byId('instruction-zoom-out').addEventListener('click', () => { instructionWidth = Math.max(24, instructionWidth - 24); renderInstruction(); });
  byId('instruction-zoom-in').addEventListener('click', () => { instructionWidth = Math.min(288, instructionWidth + 24); renderInstruction(); });
  byId('instruction-fit').addEventListener('click', () => {
    const layout = ExecutionViews.instructionLayout(steps(), loopProjections, expandedLoops, stepIndex, historyLoop, historyIteration);
    instructionWidth = Math.max(24, Math.min(288, (byId('instruction-view').clientWidth - 176) / Math.max(1, layout.blocks.length)));
    renderInstruction();
  });
  const splitter = byId('pane-splitter');
  const workspace = document.querySelector('.workspace');
  function resizeTimeline(height) {
    const total = workspace.clientHeight;
    const next = Math.max(130, Math.min(total - 170, height));
    workspace.style.setProperty('--timeline-height', `${next}px`);
    splitter.setAttribute('aria-valuenow', String(Math.round(next / total * 100)));
  }
  splitter.addEventListener('pointerdown', event => { splitter.setPointerCapture(event.pointerId); });
  splitter.addEventListener('pointermove', event => {
    if (splitter.hasPointerCapture(event.pointerId)) resizeTimeline(event.clientY - workspace.getBoundingClientRect().top);
  });
  splitter.addEventListener('pointerup', event => { splitter.releasePointerCapture(event.pointerId); });
  splitter.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    resizeTimeline(document.querySelector('.overview-pane').clientHeight + (event.key === 'ArrowUp' ? -20 : 20));
  });
  window.addEventListener('resize', () => { resizeTimeline(document.querySelector('.overview-pane').clientHeight); renderViews(); });
  byId('time-focus').addEventListener('click', () => { focusedTime = true; renderTime(); });
  byId('time-all').addEventListener('click', () => { focusedTime = false; renderTime(); });
  document.querySelector('.source-reference').addEventListener('toggle', () => { if (steps()[stepIndex]) showSource(steps()[stepIndex]); });
  loadCase();
  resizeTimeline(document.querySelector('.overview-pane').clientHeight);
})();
