/* Local import only. The E1.19 editor and its mapping state stay independent. */
(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('e127-data').textContent);
  const core = window.ExpressionObservationCore;
  const $ = id => document.getElementById(id);
  const names = {
    angry:'生气', beat_swing:'摆动', confident:'自信', confused:'困惑',
    crying:'哭泣', delicious:'美味', embarrassed:'害羞', eat:'进食',
    funny:'有趣', happy:'开心', idle:'待机', insert:'装入',
    laughing:'大笑', listen:'聆听', look_around:'环顾', look_left:'向左看',
    look_right:'向右看', loving:'喜爱', neutral:'中性', question:'疑问',
    relaxed:'放松', sad:'难过', shocked:'震惊', silly:'调皮',
    sleepy:'困倦', surprised:'惊讶', thinking:'思考', tried:'尝试',
    winking:'眨眼', other:'其他（不推断映射）'
  };
  let current = null;
  let selected = null;
  let reportedSelected = null;
  let observedSemantics = new Set();

  function escapeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({
      '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
    }[c]));
  }
  function showMessage(message) {
    const node = $('e127-message');
    node.textContent = message;
    node.hidden = !message;
  }
  function openSemantic(semantic) {
    let buttons = [...document.querySelectorAll('#semantic-list [data-semantic]')];
    let button = buttons.find(item => item.dataset.semantic === semantic);
    if (!button && $('search').value) {
      $('search').value = '';
      $('search').dispatchEvent(new Event('input', {bubbles: true}));
      buttons = [...document.querySelectorAll('#semantic-list [data-semantic]')];
      button = buttons.find(item => item.dataset.semantic === semantic);
    }
    if (button) button.click();
  }
  function highlight(semantic) {
    document.querySelectorAll('#semantic-list [data-semantic]').forEach(node => {
      node.classList.toggle('e127-observed', observedSemantics.has(node.dataset.semantic));
    });
    document.querySelectorAll('.e127-active-observed').forEach(node => node.classList.remove('e127-active-observed'));
    if (semantic) {
      const observed = document.querySelector(`#e127-observed-list [data-semantic="${semantic}"]`);
      if (observed) observed.classList.add('e127-active-observed');
    }
  }
  function highlightReported(semantic) {
    const reported = new Set(D.oralSummary.reported_semantics.map(item => item.semantic));
    document.querySelectorAll('#semantic-list [data-semantic]').forEach(node => {
      node.classList.toggle('e127-reported', reported.has(node.dataset.semantic));
      node.classList.toggle('e127-active-reported', Boolean(semantic) && node.dataset.semantic === semantic);
    });
    document.querySelectorAll('#e127-oral-list [data-semantic]').forEach(node => {
      node.classList.toggle('e127-active-reported', Boolean(semantic) && node.dataset.semantic === semantic);
    });
  }
  function renderOralSummary() {
    const summary = D.oralSummary;
    $('e127-oral-count').textContent = `口述最近记录：${summary.reported_recent_record_count} 条；happy、embarrassed、sleepy 三类合计占多数；单类次数未知。`;
    $('e127-oral-list').replaceChildren();
    for (const item of summary.reported_semantics) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'e127-semantic e127-reported';
      button.dataset.semantic = item.semantic;
      const resource = D.reference[item.semantic]?.resource_id || '无静态资源映射';
      button.innerHTML = `${escapeText(item.semantic)} · ${escapeText(names[item.semantic] || item.semantic)}<span class="count">${escapeText(item.qualitative_frequency)} · 次数未知</span><small>${escapeText(resource)}</small>`;
      button.addEventListener('click', () => {
        reportedSelected = item.semantic;
        highlightReported(reportedSelected);
        openSemantic(reportedSelected);
      });
      $('e127-oral-list').append(button);
    }
    $('e127-oral-limit').textContent = summary.evidence_note;
  }
  function render(snapshot, origin) {
    current = core.validate(snapshot, D.schemaDefinition);
    const summary = core.summarize(current, D.reference);
    observedSemantics = new Set(summary.semantics.map(item => item.semantic));
    $('e127-origin').textContent = origin;
    $('e127-summary').innerHTML = [
      ['记录', summary.recordCount], ['调用至少', summary.callsAtLeast],
      ['不同语义', summary.semantics.length], ['覆盖', summary.overwritten],
      ['锁竞争丢弃', summary.contentionDrops], ['序号耗尽', summary.sequenceDrops],
      ['时间回绕', summary.wrappedRecords]
    ].map(([label, value]) => `<span class="e127-stat">${escapeText(label)} <strong>${escapeText(value)}</strong></span>`).join('');
    const semantics = [...summary.semantics].sort((a, b) => a.firstSequence - b.firstSequence);
    $('e127-observed-list').replaceChildren();
    if (!semantics.length) {
      const empty = document.createElement('div');
      empty.className = 'e127-empty';
      empty.textContent = '尚无 SetEmotion 调用记录';
      $('e127-observed-list').append(empty);
    } else {
      for (const item of semantics) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'e127-semantic';
        button.dataset.semantic = item.semantic;
        button.innerHTML = `${escapeText(item.semantic)} · ${escapeText(names[item.semantic] || item.semantic)}<span class="count">×${item.callsAtLeast}</span>`;
        button.addEventListener('click', () => {
          selected = item.semantic;
          highlight(selected);
          openSemantic(selected);
        });
        $('e127-observed-list').append(button);
      }
    }
    $('e127-records').replaceChildren();
    for (const row of current.records) {
      const tr = document.createElement('tr');
      tr.tabIndex = 0;
      tr.dataset.semantic = row.semantic;
      tr.innerHTML = `<td class="mono">#${escapeText(row.sequence)}</td><td>${escapeText(row.semantic)} · ${escapeText(names[row.semantic] || row.semantic)}</td><td>${escapeText(row.first_seen_ms)} / ${escapeText(row.last_seen_ms)} ms</td><td>${escapeText(row.repeat_count)}</td>`;
      const activate = () => {
        selected = row.semantic;
        highlight(selected);
        openSemantic(selected);
      };
      tr.addEventListener('click', activate);
      tr.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); } });
      $('e127-records').append(tr);
    }
    highlight(selected);
    highlightReported(reportedSelected);
    showMessage('');
  }
  function restoreSample() {
    render(D.sample.snapshot, '来源：离线合成样例（不是设备运行轨迹）');
  }
  function showNoRawSnapshot() {
    current = null;
    observedSemantics = new Set();
    $('e127-origin').textContent = '尚未导入设备原始 JSON；下方口述摘要独立保存。离线示例需点击按钮后加载。';
    $('e127-summary').replaceChildren();
    $('e127-observed-list').replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'e127-empty';
    empty.textContent = '没有可核验的原始快照；口述语义见上方青色虚线卡片。';
    $('e127-observed-list').append(empty);
    $('e127-records').replaceChildren();
    highlight(null);
    highlightReported(reportedSelected);
  }
  $('e127-sample').addEventListener('click', restoreSample);
  $('e127-file').addEventListener('change', event => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (file.size > core.MAX_FILE_BYTES) { showMessage('拒绝导入：JSON 快照不得超过 64 KiB'); event.target.value = ''; return; }
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const snapshot = core.parse(String(reader.result), D.schemaDefinition);
        render(snapshot, `来源：本地文件 ${file.name}（仅导入，不连接设备）`);
      } catch (error) {
        showMessage(`拒绝导入：${error.message}`);
      } finally { event.target.value = ''; }
    };
    reader.onerror = () => { showMessage('拒绝导入：无法读取本地文件'); event.target.value = ''; };
    reader.readAsText(file);
  });
  const list = $('semantic-list');
  if (list) new MutationObserver(() => { highlight(selected); highlightReported(reportedSelected); }).observe(list, {childList: true});
  renderOralSummary();
  showNoRawSnapshot();
})();
