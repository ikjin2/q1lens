const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { _electron } = require('playwright');
const { analyzeProject } = require('../analysis.cjs');

const root = path.resolve(__dirname, '../..');
const demo = path.join(root, 'examples/acquisition-window-prototype');
const project = path.join(demo, 'q1timeline.yml');
const output = path.join(root, 'output/playwright');

(async () => {
  await fs.mkdir(output, { recursive: true });
  const { ir } = await analyzeProject(project);
  const acquires = (id) => ir.events.filter(event => event.kind === 'acquire' && event.sequencer_id === id);
  const windows = acquires('readout').map(event => event.meta.acquisition_window);
  assert.deepEqual(windows.map(window => [window.start_ns, window.end_ns, window.effective_duration_ns, window.status]),
    [[100, 700, 600, 'interrupted'], [700, 1700, 1000, 'completed'], [2000, 3000, 1000, 'completed']]);
  assert.deepEqual(acquires('parallel').map(event => [event.meta.acquisition_window.start_ns, event.meta.acquisition_window.end_ns]),
    [[300, 1100], [1500, 2300]]);
  assert.ok(acquires('unknown').every(event => event.meta.acquisition_window.end_ns === null));
  assert.ok(ir.events.filter(event => event.kind === 'acquire').every(event => event.duration.value === 4));

  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const app = await _electron.launch({ args: [path.join(root, 'desktop-app/main.cjs')], env });
  const errors = [];
  const checks = ['actual Python analysis keeps RT waits at 4 ns and computes independent integration windows'];
  const evidence = {};
  let page;
  try {
    page = await app.firstWindow();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.waitForFunction(() => window.q1lensTimeline?.getSnapshot().ready && document.getElementById('run-status').textContent === 'READY', null, { timeout: 90000 });
    const snapshot = () => page.evaluate(() => window.q1lensTimeline.getSnapshot());
    async function open(file) {
      await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, file);
      await page.locator('#open-project').click();
      await page.waitForFunction(file => document.getElementById('project-name').title === file && !document.getElementById('open-project').disabled, file, { timeout: 90000 });
      assert.equal(await page.locator('#run-status').textContent(), 'READY');
    }
    async function selectWindow(sequencerId, index) {
      const state = await snapshot();
      const window = state.acquisitionRows.find(row => row.sequencerId === sequencerId).windows[index];
      const box = await page.locator('#pixi-stage canvas').boundingBox();
      await page.mouse.click(box.x + window.x + Math.max(1, window.width / 2), box.y + window.y + window.height / 2);
      assert.equal((await snapshot()).selected?.event?.id, window.sourceEventId);
      assert.equal((await snapshot()).selected?.event?.kind, 'acquire');
      return page.locator('#inspector-details').innerText();
    }
    async function assertPrimaryDetailsVisible() {
      const fields = await page.evaluate(() => {
        const panel = document.querySelector('.side-panel').getBoundingClientRect();
        return ['Effective integration', 'Integration status', 'RT wait'].map(label => {
          const field = [...document.querySelectorAll('#inspector-details .field')].find(field => field.querySelector('span').textContent === label);
          const r = field.getBoundingClientRect();
          return { label, visible: r.top >= panel.top && r.bottom <= panel.bottom && r.bottom <= innerHeight };
        });
      });
      assert.ok(fields.every(field => field.visible), `Primary integration details must be visible without sidebar scrolling: ${JSON.stringify(fields)}`);
    }
    const assertRows = (state) => {
      assert.equal(state.sequencerCount, 3);
      assert.deepEqual(state.acquisitionRows.map(row => [row.sequencerId, row.windows.length]),
        [['readout', 3], ['parallel', 2], ['unknown', 2]]);
      assert.ok(state.acquisitionRows.every(row => row.height === 26));
    };
    await open(project);
    const initial = await snapshot();
    assertRows(initial);
    const offset = acquires('readout')[0].meta.aligned_t0 - acquires('readout')[0].t0.value;
    assert.deepEqual(initial.acquisitionRows[0].windows.map(window => [window.startNs, window.endNs]),
      [[100 + offset, 700 + offset], [700 + offset, 1700 + offset], [2000 + offset, 3000 + offset]]);
    assert.ok(initial.acquisitionRows[2].windows.every(window => window.endNs === null && window.status === 'unknown'));
    evidence.initial = initial;
    checks.push('native project open analyzes real YAML; seven acquisitions occupy exactly three ACQ rows');
    await page.screenshot({ path: path.join(output, 'acquisition-overview.png') });

    let details = await selectWindow('readout', 0);
    assert.match(details, /RT wait\s+4 ns/);
    assert.match(details, /Effective integration\s+600 ns \/ 1,?000 ns configured/);
    assert.match(details, /Integration status\s+Interrupted/i);
    assert.match(details, /Interrupted by/);
    assert.match(details, /Integration source\s+.*hardware/i);
    await assertPrimaryDetailsVisible();
    await page.screenshot({ path: path.join(output, 'acquisition-interrupted.png') });
    evidence.interruptedInspector = details;
    details = await selectWindow('readout', 1);
    assert.match(details, /Effective integration\s+1,?000 ns/);
    assert.match(details, /Integration status\s+Completed/i);
    evidence.completedInspector = details;
    details = await selectWindow('unknown', 0);
    assert.match(details, /Effective integration\s+Unknown integration length/i);
    assert.match(details, /Integration status\s+Unknown/i);
    assert.doesNotMatch(details, /Effective integration\s+4 ns/);
    await page.screenshot({ path: path.join(output, 'acquisition-unknown.png') });
    evidence.unknownInspector = details;
    checks.push('mouse selection resolves original Acquire; inspector distinguishes 4 ns wait, configured/effective integration and unknown');

    for (const [button, mode] of [['showcase-mode', 'showcase'], ['debug-mode', 'debug'], ['normal-mode', 'normal']]) {
      await page.locator(`#${button}`).click();
      const state = await snapshot();
      assert.equal(state.viewMode, mode);
      assertRows(state);
      await page.screenshot({ path: path.join(output, `acquisition-${mode}.png`) });
    }
    const canvas = await page.locator('#pixi-stage canvas').boundingBox();
    await page.mouse.move(canvas.x + canvas.width * .65, canvas.y + 100);
    await page.mouse.wheel(0, -200);
    await page.waitForFunction(fullSpan => {
      const timeWindow = window.q1lensTimeline.getSnapshot().timeWindow;
      return timeWindow.maxNs - timeWindow.minNs < fullSpan;
    }, initial.timeWindow.maxNs - initial.timeWindow.minNs);
    await page.locator('#fit-all').click();
    assert.deepEqual((await snapshot()).timeWindow, (await snapshot()).fullTimeExtent);
    checks.push('Overview, Flow and Debug preserve one ACQ row per sequencer; zoom and Fit remain functional');

    const temporaryProject = path.join(output, 'acquisition-settings-refresh.yml');
    const source = await fs.readFile(project, 'utf8');
    const absoluteFiles = source.replace(/file: (readout|parallel|unknown)\.q1asm/g,
      (_, name) => `file: ${JSON.stringify(path.join(demo, `${name}.q1asm`))}`);
    await fs.writeFile(temporaryProject, absoluteFiles.replace('integration_length_acq: 1000', 'integration_length_acq: 1200'));
    await open(temporaryProject);
    assert.equal((await snapshot()).acquisitionRows[0].windows[1].endNs, 1900 + offset);
    await fs.writeFile(temporaryProject, absoluteFiles.replace('integration_length_acq: 1000', 'integration_length_acq: 800'));
    await page.locator('#reanalyze').click();
    await page.waitForFunction(() => !document.getElementById('reanalyze').disabled, null, { timeout: 90000 });
    assert.equal((await snapshot()).acquisitionRows[0].windows[1].endNs, 1500 + offset);
    checks.push('changing saved integration setting and Reanalyze updates ACQ end without editing Q1ASM');

    await open(project);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1100, 760));
    await page.waitForFunction(() => document.querySelector('#pixi-stage canvas').getBoundingClientRect().height === document.getElementById('pixi-stage').clientHeight);
    await page.locator('#fit-all').click();
    await selectWindow('readout', 0);
    await assertPrimaryDetailsVisible();
    const fit = await page.evaluate(() => {
      const selectors = ['.timeline-panel', '.transport', '.side-panel', '.status-bar'];
      return { width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth,
        regions: selectors.map(selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { selector, left: r.left, right: r.right, top: r.top, bottom: r.bottom }; }) };
    });
    assert.ok(fit.scrollWidth <= fit.width);
    assert.ok(fit.regions.every(r => r.left >= 0 && r.top >= 0 && r.right <= fit.width && r.bottom <= fit.height));
    await page.screenshot({ path: path.join(output, 'acquisition-compact.png') });
    evidence.compact = fit;
    checks.push('1100×760 viewport keeps timeline, interrupted inspector and primary controls accessible');
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'acquisition-verification.json'), JSON.stringify({ checks, evidence, errors }, null, 2));
    console.log('PASS', JSON.stringify(checks));
  } catch (error) {
    console.error('ERRORS', JSON.stringify(errors));
    if (page) {
      console.error('BODY', await page.locator('body').innerText());
      await page.screenshot({ path: path.join(output, 'acquisition-failure.png') });
    }
    throw error;
  } finally {
    await app.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
