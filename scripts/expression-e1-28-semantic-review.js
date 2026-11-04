(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('e128-data').textContent);
  const $ = id => document.getElementById(id);
  const semantics = D.semantics;
  const canvases = new Map();
  let selected = 'happy';
  let playing = false;
  let clock = 0;
  let raf = 0;
  let origin = 0;
  let previewFps = 20;
  const imageCache = new Map();

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function assetFor(semantic) { return D.assets[D.bindings[semantic].resource_id]; }
  function load(resource) {
    if (imageCache.has(resource)) return imageCache.get(resource);
    const image = new Image();
    imageCache.set(resource, image);
    image.onload = () => canvases.forEach((target, canvas) => { if (target.resource === resource) paint(canvas); });
    image.src = D.assets[resource].atlas;
    return image;
  }
  function frameIndex(binding) {
    const asset = D.assets[binding.resource_id];
    const frame = Math.floor(clock * previewFps + 1e-6);
    return binding.loop ? frame % asset.frame_count : Math.min(asset.frame_count - 1, frame);
  }
  function paint(canvas) {
    const target = canvases.get(canvas);
    if (!target || !canvas.isConnected) return;
    const binding = D.bindings[target.semantic];
    const asset = assetFor(target.semantic);
    const image = load(binding.resource_id);
    const ctx = canvas.getContext('2d');
    const frame = frameIndex(binding);
    const key = `${target.semantic}/${frame}`;
    // An embedded atlas loads asynchronously. Do not mark a frame as painted
    // until the pixels have actually been drawn, otherwise the image onload
    // callback can see the same key and incorrectly skip the first render.
    if (canvas.dataset.paintKey === key && image.complete && image.naturalWidth) return;
    ctx.clearRect(0, 0, 360, 360);
    ctx.fillStyle = '#030505';
    ctx.fillRect(0, 0, 360, 360);
    if (!image.complete || !image.naturalWidth) return;
    const w = asset.frame_width, h = asset.frame_height;
    const sx = (frame % asset.columns) * w;
    const sy = Math.floor(frame / asset.columns) * h;
    ctx.save();
    ctx.beginPath(); ctx.arc(180, 180, 180, 0, Math.PI * 2); ctx.clip();
    const cw = Math.min(w, 340), y = Math.floor((360 - h) / 2) + 10;
    ctx.drawImage(image, sx, sy, cw, h, 10, y, cw, h);
    ctx.translate(350, 0); ctx.scale(-1, 1);
    ctx.drawImage(image, sx, sy, cw, h, 0, y, cw, h);
    ctx.restore();
    canvas.dataset.paintKey = key;
  }
  function createPreview(parent, semantic) {
    const canvas = document.createElement('canvas');
    canvas.width = 360; canvas.height = 360; canvas.className = 'preview';
    canvas.setAttribute('aria-label', `${semantic} 圆屏动画预览，官方解码帧参考`);
    canvases.set(canvas, {semantic, resource: D.bindings[semantic].resource_id});
    parent.append(canvas);
    load(D.bindings[semantic].resource_id);
    paint(canvas);
    return canvas;
  }
  function setSelected(semantic) {
    selected = semantic;
    document.querySelectorAll('.semantic-card').forEach(card => card.classList.toggle('selected', card.dataset.semantic === semantic));
    document.querySelectorAll('.oral-item').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.semantic === semantic)));
    const item = D.semantics.find(row => row.id === semantic);
    if (item) $('selected-detail').textContent = `${item.id} · ${item.label_zh}：${item.trigger_summary}`;
    paintAll();
  }
  function renderCard(item) {
    const binding = D.bindings[item.id];
    const asset = D.assets[binding.resource_id];
    const card = document.createElement('article');
    card.className = 'semantic-card' + (item.shared_resource ? ' shared' : '');
    card.dataset.semantic = item.id;
    const chipClass = item.trigger_kind === 'dynamic' ? 'dynamic' : 'literal';
    card.innerHTML = `<div class="card-head"><div><div class="card-title">${esc(item.id)}</div><div class="card-label">${esc(item.label_zh)}</div></div><span class="card-chip ${chipClass}">${esc(item.trigger_kind_label)}</span></div>`;
    createPreview(card, item.id);
    const meta = document.createElement('dl'); meta.className = 'card-meta';
    const rows = [['EAF', binding.resource_id, 'resource'], ['动画', `${asset.frame_count} 帧 · ${binding.fps} FPS · ${binding.loop ? '循环' : '单次'}`, ''], ['动作请求', binding.action_id ? `${binding.action_label} / ${binding.action_id}` : '无固定动作', binding.action_id ? 'action' : ''], ['布局', binding.layout_id, 'mono']];
    meta.innerHTML = rows.map(([key, value, cls]) => `<dt>${esc(key)}</dt><dd class="${cls}">${esc(value)}</dd>`).join('');
    card.append(meta);
    const sourceList = document.createElement('div'); sourceList.className = 'source-list';
    for (const source of item.sources) {
      const row = document.createElement('div'); row.className = 'source-item';
      row.innerHTML = `<strong>${esc(source.label)}</strong><br>${esc(source.detail)}<br><span class="mono">${esc(source.ref)}</span>`;
      sourceList.append(row);
    }
    card.append(sourceList);
    const evidence = document.createElement('div'); evidence.className = 'evidence';
    evidence.innerHTML = `<strong>证据边界：</strong>${esc(item.evidence_scope)}`;
    card.append(evidence);
    card.addEventListener('click', event => { if (!event.target.closest('button')) setSelected(item.id); });
    return card;
  }
  function renderGraph() {
    const root = $('graph-grid');
    for (const item of semantics) {
      const binding = D.bindings[item.id];
      const asset = D.assets[binding.resource_id];
      const col = document.createElement('section'); col.className = 'graph-col';
      col.innerHTML = `<h3>${esc(item.id)} · ${esc(item.label_zh)}</h3><div class="flow"><div class="flow-node trigger"><div class="flow-kind">触发 / 入口</div><div class="flow-title">${esc(item.trigger_title)}</div><div class="flow-detail">${esc(item.trigger_summary)}</div></div><div class="arrow">→</div><div class="flow-node call"><div class="flow-kind">调用</div><div class="flow-title">SetEmotion("${esc(item.id)}")</div><div class="flow-detail">${esc(item.call_detail)}</div></div><div class="branch"><div class="branch-line"><div class="branch-arrow">↘</div><div class="flow-node resource"><div class="flow-kind">表情资源 / EAF 输入</div><div class="flow-title">${esc(binding.resource_id)}</div><div class="flow-detail">${asset.frame_count} 帧 · ${binding.loop ? '循环' : '单次'} · ${esc(binding.layout_id)}</div></div></div><div class="branch-line"><div class="branch-arrow">↗</div><div class="flow-node action"><div class="flow-kind">独立动作请求</div><div class="flow-title">${esc(binding.action_id || '无固定动作')}</div><div class="flow-detail">${esc(item.action_detail)}</div></div></div></div></div><p class="graph-note">${esc(item.graph_note)}</p></section>`;
      root.append(col);
    }
  }
  function paintAll() {
    canvases.forEach((_, canvas) => paint(canvas));
    const binding = D.bindings[selected];
    $('frame-value').textContent = `${frameIndex(binding) + 1} / ${D.assets[binding.resource_id].frame_count} 帧`;
  }
  function stop() { playing = false; if (raf) cancelAnimationFrame(raf); raf = 0; $('play').textContent = '播放四类预览'; $('play').setAttribute('aria-pressed', 'false'); }
  function tick(now) { raf = 0; if (!playing) return; clock = (now - origin) / 1000; paintAll(); raf = requestAnimationFrame(tick); }
  function start() { if (playing) return; playing = true; origin = performance.now() - clock * 1000; $('play').textContent = '暂停预览'; $('play').setAttribute('aria-pressed', 'true'); raf = requestAnimationFrame(tick); }
  $('semantic-grid').replaceChildren(...semantics.map(renderCard));
  $('graph-grid').replaceChildren(); renderGraph();
  for (const item of D.oral_summary.reported_semantics) {
    const binding = D.bindings[item.semantic];
    const button = document.createElement('button'); button.type = 'button'; button.className = 'oral-item'; button.dataset.semantic = item.semantic; button.setAttribute('aria-pressed', 'false');
    button.innerHTML = `<strong>${esc(item.semantic)} · ${esc(item.label_zh)}</strong><small>${esc(binding.resource_id)} · ${esc(item.qualitative_frequency)}</small>`;
    button.addEventListener('click', () => setSelected(item.semantic)); $('oral-grid').append(button);
  }
  document.querySelectorAll('.semantic-card').forEach(card => card.addEventListener('dblclick', () => setSelected(card.dataset.semantic)));
  $('play').addEventListener('click', () => playing ? stop() : start());
  $('step').addEventListener('click', () => { stop(); clock += 1 / previewFps; paintAll(); });
  $('reset').addEventListener('click', () => { stop(); clock = 0; paintAll(); });
  $('speed').addEventListener('input', event => {
    const nextFps = Number(event.target.value);
    clock *= previewFps / nextFps;
    previewFps = nextFps;
    origin = performance.now() - clock * 1000;
    $('speed-value').textContent = `${previewFps} FPS`;
    paintAll();
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  setSelected(selected);
  document.body.dataset.ready = 'true';
  paintAll();
})();
