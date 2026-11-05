/* The renderer only consumes instruction events, snapshots, loops and operands.
 * There are no per-program visualization branches.
 */
(() => {
  'use strict';
  const T = window.ExecutionTrace, $ = id => document.getElementById(id);
  const escape = text => String(text ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
  const number = value => value === undefined ? '—' : T.s32(value).toLocaleString('en-US');
  const raw = value => value === undefined ? '미정' : `signed ${T.s32(value)} · unsigned ${value} · 0x${value.toString(16).padStart(8, '0')}`;
  let sample, program, trace, rows = [], loop, watched = [], rowIndex = 0, focusId = null, focusRegister = null, expanded = false;
  const registerName = register => program.aliases[register] ? `${register} · ${program.aliases[register]}` : register;
  const allRegisters = () => [...new Set(trace.events.flatMap(event => event.writes.map(write => write.register)))].sort((a,b) => Number(a.slice(1)) - Number(b.slice(1)));
  const current = () => rows[rowIndex];
  const baseline = () => {
    const selected = $('baseline').value;
    return selected === 'previous' ? rowIndex ? rows[rowIndex - 1].after : current().before : rows[Number(selected)].after;
  };
  function changedRegisters(row, base) {
    return allRegisters().filter(register => row.after.values[register] !== base.values[register]);
  }
  function recommendRegisters() {
    const changed = new Set(rows.flatMap((row, i) => changedRegisters(row, i ? rows[i-1].after : row.before)));
    const carried = new Set(), compared = new Set(), written = new Set();
    for (const event of rows[0]?.events ?? []) {
      for (const read of event.reads) if (!written.has(read.register)) carried.add(read.register);
      event.writes.forEach(write => written.add(write.register));
    }
    for (const event of trace.events) if (event.op === 'cmp' || (event.taken !== undefined && event.op !== 'loop')) event.reads.forEach(read => compared.add(read.register));
    return allRegisters().filter(register => changed.has(register)).sort((a,b) =>
      (Number(carried.has(b))*3 + Number(compared.has(b))*2) - (Number(carried.has(a))*3 + Number(compared.has(a))*2)
      || Number(a.slice(1))-Number(b.slice(1))).slice(0, 6);
  }
  function loadSample(id) {
    sample = window.EXPLORER_EXAMPLES.find(item => item.id === id);
    program = T.parse(sample.source, sample.params);
    $('file-name').textContent = sample.file.split('/').at(-1);
    $('file-name').title = sample.file;
    $('inputs').value = sample.inputs.join(', ');
    $('input-editor').hidden = !sample.usesInputs;
    $('input-error').textContent = '';
    rerun(sample.inputs);
  }
  function rerun(inputs) {
    trace = T.execute(program, inputs);
    loop = program.loops[0];
    if (!loop) throw new Error('이 데모에서 비교할 뒤쪽 분기 반복을 찾지 못했습니다.');
    $('run-status').textContent = trace.status === 'complete' ? `실행 완료 · ${trace.events.length.toLocaleString()}개 명령` : '실행 대기 · 입력 또는 지원 범위 확인';
    $('run-status').classList.toggle('pending', trace.status !== 'complete');
    $('input-summary').textContent = sample.usesInputs ? `가상 정수 입력 · ${trace.inputCount} / ${inputs.length}개 사용` : '외부 입력 없음 · 프로그램 상수로 실행';
    renderLoops(); selectLoop(loop.name);
  }
  function renderLoops() {
    $('loops').innerHTML = program.loops.map(item => {
      const count = T.iterations(trace, item).length;
      const nested = program.loops.some(parent => parent !== item && parent.start < item.start && parent.end >= item.end);
      return `<button type="button" data-loop="${escape(item.name)}" aria-pressed="${item.name === loop.name}" class="${nested ? 'nested' : ''}"><span class="mono">↳ @${escape(item.name)}</span><small>${count}회 실행 · L${program.instructions[item.start].line}–${program.instructions[item.end].line}</small></button>`;
    }).join('');
    $('loops').querySelectorAll('button').forEach(button => button.onclick = () => selectLoop(button.dataset.loop));
  }
  function selectLoop(name) {
    loop = program.loops.find(item => item.name === name);
    rows = T.iterations(trace, loop);
    if (!rows.length) throw new Error('선택한 반복에 실행 기록이 없습니다.');
    rowIndex = Math.min(2, rows.length - 1); watched = recommendRegisters();
    $('baseline').innerHTML = '<option value="previous">이전 회차</option>' + rows.map((row,index) => `<option value="${index}">${index+1}회차 끝</option>`).join('');
    $('loop-title').textContent = `@${loop.name}`;
    $('loop-description').textContent = `${sample.file.split('/').at(-1)} · ${rows.length}개 회차 · 같은 실행 지점에서 상태 비교`;
    $('loops').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.loop === name)));
    $('watch-options').innerHTML = allRegisters().map(register => `<label><input type="checkbox" value="${register}" ${watched.includes(register)?'checked':''}>${escape(registerName(register))}</label>`).join('');
    $('watch-options').querySelectorAll('input').forEach(input => input.onchange = () => {
      watched = [...$('watch-options').querySelectorAll('input:checked')].map(input => input.value);
      renderMain(); renderInspector();
    });
    chooseFocus(); renderMain(); renderInspector();
  }
  function chooseFocus(register) {
    const row = current(), changes = changedRegisters(row, baseline());
    if (!register && trace.events[row.end].blocked) { focusRegister = null; focusId = row.end; expanded = false; return; }
    const feedbackDriven = watched.find(item => {
      const writer = trace.events[row.after.writers[item]], branch = writer?.control;
      return changes.includes(item) && branch != null && [...T.causes(trace, branch)].some(id => trace.events[id].input);
    });
    focusRegister = register ?? feedbackDriven ?? watched.find(item => changes.includes(item)) ?? watched[0];
    focusId = row.after.writers[focusRegister] ?? row.events.filter(event => event.taken !== undefined).at(-1)?.id ?? row.end;
    expanded = false;
  }
  function selectRow(index, register) {
    rowIndex = Math.min(rows.length - 1, Math.max(0, index));
    chooseFocus(register); renderMain(); renderInspector();
  }
  function selectEvent(id) { focusId = Number(id); focusRegister = null; expanded = false; renderInspector(); renderSource(); }
  function renderMain() {
    $('iteration-count').textContent = `${rowIndex + 1} / ${rows.length}`;
    $('previous').disabled = rowIndex === 0; $('next').disabled = rowIndex === rows.length - 1;
    $('iteration-table').querySelector('thead').innerHTML = `<tr><th>회차</th>${watched.map(register => `<th>${register}${program.aliases[register] ? `<small>${escape(program.aliases[register])}</small>` : ''}</th>`).join('')}</tr>`;
    $('iteration-table').querySelector('tbody').innerHTML = rows.map((row,index) => {
      const prev = index ? rows[index - 1].after : row.before;
      const last = trace.events[row.end];
      return `<tr class="${index === rowIndex ? 'selected' : ''}" data-row="${index}"><td><button type="button" data-row="${index}" aria-label="${index+1}회차 선택">${index+1}회차${last.blocked ? '<span class="row-exit">입력 대기 / 중단</span>' : row.partial ? '<span class="row-exit">부분 기록</span>' : row.exit ? '<span class="row-exit">반복 종료</span>' : ''}</button></td>${watched.map(register => {
        const value = row.after.values[register], before = prev.values[register], changed = value !== before;
        return `<td class="${changed?'changed':''} ${index===rowIndex && register===focusRegister?'selected-cell':''}"><button type="button" data-row="${index}" data-register="${register}" title="${escape(raw(value))}" aria-label="${index+1}회차 ${escape(registerName(register))} = ${number(value)}, 이전 ${number(before)}. 원인 추적">${number(value)}${changed ? `<span class="old-value">← ${number(before)}</span>` : ''}</button></td>`;
      }).join('')}</tr>`;
    }).join('');
    $('iteration-table').querySelectorAll('button').forEach(button => button.onclick = () => selectRow(Number(button.dataset.row), button.dataset.register));
    $('checkpoint-title').textContent = `${rowIndex+1}회차 · ${$('baseline').value === 'previous' ? rowIndex ? '이전 회차와 비교' : '진입 상태와 비교' : `${Number($('baseline').value)+1}회차와 비교`}`;
    const before = baseline(), row = current();
    const changes = changedRegisters(row,before).filter(register => watched.includes(register));
    $('changes').innerHTML = changes.length ? changes.map(register => `<button type="button" data-register="${register}" class="${register===focusRegister?'active':''}" title="${escape(raw(row.after.values[register]))}"><code>${escape(registerName(register))}</code> ${number(before.values[register])} → ${number(row.after.values[register])}</button>`).join('') : '<span class="empty">이 비교 기준에서는 레지스터 값이 같습니다. 아래 분기를 선택해 판단 근거를 확인할 수 있습니다.</span>';
    if (trace.events[row.end].blocked) $('changes').innerHTML += `<div class="blocked-banner">${escape(trace.message)} 이후 값은 계산하지 않았습니다.</div>`;
    $('changes').querySelectorAll('button').forEach(button => button.onclick = () => { chooseFocus(button.dataset.register); renderMain(); renderInspector(); });
    const groups = new Map();
    for (const event of row.events) if (event.taken !== undefined) {
      const entry = groups.get(event.pc) ?? { count: 0 }; groups.set(event.pc, { count: entry.count+1, event });
    }
    $('decisions').innerHTML = [...groups.values()].map(({event,count}) => `<button type="button" data-event="${event.id}"><span>L${event.line} ${event.op}</span><span class="path">${event.taken?'Jump':'Continue'}</span>${count>1 ? `<small>${count}회 중 마지막</small>` : ''}</button>`).join('') || '<span class="empty-inspector">기록된 분기 없음</span>';
    $('decisions').querySelectorAll('button').forEach(button => button.onclick = () => selectEvent(button.dataset.event));
    renderSource();
  }
  function nodeHTML(id, depth, seen) {
    const event = trace.events[id];
    if (seen.has(id)) return `<div class="cause-node shared"><button type="button" data-event="${id}">E${id+1} · 공유된 원인 보기</button></div>`;
    seen.add(id);
    const write = event.writes.map(write => `${write.register} = ${number(write.value)}`).join(' · ');
    const title = event.blocked ? '입력 대기' : write || (event.taken !== undefined ? `${event.taken?'Jump':'Continue'} · ${event.condition}` : event.op);
    return `<details class="cause-node" ${expanded || depth<2 ? 'open' : ''}><summary><strong>${escape(title)}</strong><span class="cause-meta"> · E${id+1}</span></summary><button type="button" data-event="${id}">L${event.line} ${escape(event.op+' '+event.args.join(', '))}</button>${event.input ? `<div class="input-root">가상 입력 #${event.input.index+1} · channel ${event.input.channel}<br>${number(event.input.value)} → ${escape(event.writes[0].register)}</div>` : ''}${!event.parents.length && !event.input ? `<div class="literal">${event.blocked?'제공되지 않은 입력에서 멈춤':'소스의 상수 / 설정값'}</div>` : ''}${event.parents.map(parent => nodeHTML(parent,depth+1,seen)).join('')}</details>`;
  }
  function renderInspector() {
    $('expand-causes').textContent = expanded ? '원인 접기' : '입력까지 펼치기';
    const event = trace.events[focusId];
    if (!event) { $('focus-summary').textContent = '아직 값이 없는 레지스터입니다.'; $('causes').innerHTML = ''; $('control-cause').innerHTML=''; $('branch-alternatives').innerHTML=''; return; }
    const writes = event.writes.filter(write => !focusRegister || write.register === focusRegister);
    const display = writes.length ? writes.map(write => `${registerName(write.register)} = ${number(write.value)}`).join(' · ') : event.taken !== undefined ? event.taken ? 'Jump' : 'Continue' : event.blocked ? '입력 대기' : event.op;
    const occurrence = trace.events.slice(0,event.id+1).filter(other => other.pc === event.pc).length;
    $('focus-summary').innerHTML = `<div class="focus-value">${escape(display)}</div><div class="focus-code">${escape(event.op+' '+event.args.join(', '))}</div><div class="focus-meta">L${event.line} · E${event.id+1} · 이 줄의 ${occurrence}번째 실행</div><div class="value-rows">${event.reads.map(read => `<span><label>${escape(registerName(read.register))}</label><b title="${escape(raw(read.value))}">${number(read.value)}</b></span>`).join('')}${event.writes.map(write => `<span class="write"><label>쓰기 ${write.register}</label><b title="${escape(raw(write.value))}">${number(write.before)} → ${number(write.value)}</b></span>`).join('')}</div>`;
    $('causes').innerHTML = nodeHTML(event.id,0,new Set());
    $('causes').querySelectorAll('button').forEach(button => button.onclick = () => selectEvent(button.dataset.event));
    const ctrl = event.control === null ? null : trace.events[event.control];
    $('control-cause').innerHTML = ctrl ? `이 명령 전에 마지막으로 평가한 분기<button type="button" data-event="${ctrl.id}">L${ctrl.line} ${escape(ctrl.op)} → ${ctrl.taken?'Jump':'Continue'} · E${ctrl.id+1}</button>` : '이 명령 전에 평가된 조건 분기가 없습니다.';
    const controlButton = $('control-cause').querySelector('button');
    if (controlButton) controlButton.onclick = () => selectEvent(controlButton.dataset.event);
    if (event.taken !== undefined) {
      const destination = event.args.at(-1);
      const target = destination?.startsWith('@') ? program.labels[destination.slice(1)] : null;
      const jumpLine = target === null ? '?' : program.instructions[target]?.line;
      $('branch-alternatives').innerHTML = `<div class="alternative ${event.taken?'chosen':''}"><span>Jump ${event.taken?'✓':''}</span><code>${escape(destination)} · L${jumpLine}</code></div><div class="alternative ${!event.taken?'chosen':''}"><span>Continue ${!event.taken?'✓':''}</span><code>L${program.instructions[event.pc+1]?.line ?? 'end'}</code></div><div class="muted">${escape(event.condition)}</div>`;
    } else $('branch-alternatives').innerHTML='';
    renderSource();
  }
  function renderSource() {
    const scroll = $('source').scrollTop;
    const event = trace.events[focusId], causal = event ? T.causes(trace,event.id) : new Set();
    if (event?.control != null) causal.add(event.control);
    const causalLines = new Set([...causal].map(id => trace.events[id].line));
    $('source-location').textContent = event ? `L${event.line} · E${event.id+1}` : '';
    $('source').innerHTML = sample.source.split(/\r?\n/).map((line,index) => {
      const comment = line.indexOf('#');
      return `<div class="source-line ${event?.line===index+1?'focused':causalLines.has(index+1)?'causal':''} ${line?'':'empty'}" data-line="${index+1}"><span class="line-number">${index+1}</span><span>${comment<0 ? escape(line) : escape(line.slice(0,comment))+`<span class="comment">${escape(line.slice(comment))}</span>`}</span></div>`;
    }).join('');
    $('source').scrollTop = scroll;
    followSource();
  }
  function followSource() {
    const selected = $('source').querySelector('.focused');
    if (!selected) return;
    const container = $('source'), top = selected.getBoundingClientRect().top-container.getBoundingClientRect().top+container.scrollTop;
    if (top < container.scrollTop+20 || top > container.scrollTop+container.clientHeight-40) container.scrollTop = Math.max(0, top-container.clientHeight/2);
  }
  $('program').innerHTML = window.EXPLORER_EXAMPLES.map(item => `<option value="${item.id}">${escape(item.title)}</option>`).join('');
  $('program').onchange = () => { try { loadSample($('program').value); } catch(error) { showError(error); } };
  $('previous').onclick = () => selectRow(rowIndex-1);
  $('next').onclick = () => selectRow(rowIndex+1);
  $('baseline').onchange = () => { chooseFocus(); renderMain(); renderInspector(); };
  $('follow-source').onclick = followSource;
  $('expand-causes').onclick = () => { expanded = !expanded; $('expand-causes').textContent=expanded?'원인 접기':'입력까지 펼치기'; renderInspector(); };
  $('apply-inputs').onclick = () => {
    const text = $('inputs').value.trim(), tokens = text ? text.split(/[\s,;]+/) : [];
    if (tokens.some(token => !/^-?\d+$/.test(token) || !Number.isSafeInteger(Number(token)) || Number(token)<-2147483648 || Number(token)>4294967295)) { $('input-error').textContent='−2,147,483,648부터 4,294,967,295까지의 정수를 입력하세요.'; return; }
    $('input-error').textContent='';
    try { rerun(tokens.map(Number)); } catch(error) { showError(error); }
  };
  function showError(error) { $('fatal-error').hidden=false; $('fatal-error').textContent=error.message; console.error(error); }
  try { loadSample(window.EXPLORER_EXAMPLES[0].id); } catch(error) { showError(error); }
})();
