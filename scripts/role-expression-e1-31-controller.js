(function () {
  'use strict';
  const D = JSON.parse(document.getElementById('e131-data').textContent);
  const ctx = {baseline: D.baseline, asset_version: D.asset_version, binding_version: D.binding_version,
    semantics: D.semantics, assets: D.assets};
  const editor = RoleExpressionDraft.create(ctx, D.initial);
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = Object.fromEntries(Object.entries(D.semantics).map(([id, x]) => [id, x.label || id]));
  let selectedSemantic = 'happy', image = null, imageResource = '', frame = 0, playing = false, raf = 0, last = 0;
  function active() { return editor.active(); }
  function activeProfile() { return active().role_profile; }
  function currentResource() { return active().semantic_resources[selectedSemantic]; }
  function baselineResource() { return D.semantics[selectedSemantic].resource_id; }
  function announce(message, error) { $('status').textContent = message; $('status').className = 'status' + (error ? ' error' : ''); }
  function roleId() { return activeProfile().role_id; }
  function renderRoles() {
    const snapshot = editor.snapshot(); $('role-select').innerHTML = '';
    for (const row of snapshot.roles) { const option = document.createElement('option'); option.value = row.role_profile.role_id; option.textContent = row.role_profile.display_name + ' · ' + row.role_profile.role_id; $('role-select').append(option); }
    $('role-select').value = snapshot.selected_role_id; $('role-count').textContent = snapshot.roles.length;
    $('role-list').replaceChildren();
    for (const row of snapshot.roles) {
      const p = row.role_profile, button = document.createElement('button'); button.type = 'button'; button.className = 'role-card' + (p.role_id === snapshot.selected_role_id ? ' active' : '');
      const changes = Object.entries(row.semantic_resources).filter(([id, value]) => value !== D.semantics[id].resource_id).length;
      button.innerHTML = `<div class="title">${esc(p.display_name)}</div><div class="line mono">${esc(p.role_id)}</div><div class="line">外壳引用 ${esc(p.shell_id)} · 磁场档案引用 ${esc(p.magnet_profile_id)}</div><div class="mini"><span>${p.binding_state} draft</span><span>${changes} 项接线变化</span></div>`;
      button.addEventListener('click', () => { editor.selectRole(p.role_id); selectedSemantic = Object.keys(D.semantics)[0]; frame = 0; render(); announce(`已切换到 ${p.display_name}；当前仍是本地草稿。`); }); $('role-list').append(button);
    }
  }
  function renderProfile() {
    const p = activeProfile();
    $('profile').innerHTML = `<div class="fact"><label for="display-name">显示名称（草稿）</label><input id="display-name" type="text" value="${esc(p.display_name)}" maxlength="80"></div><div class="fact"><label>语音 profile（示例）</label><div class="mono">${esc(p.voice_id)}</div></div><div class="fact"><label>外壳身份引用（合成草稿）</label><div class="mono">${esc(p.shell_id)}</div><div class="muted small">磁场档案引用：${esc(p.magnet_profile_id)} · 不代表实物已登记</div></div><div class="fact"><label>版本与状态</label><div class="mono">${esc(p.asset_version)}</div><div class="muted small">${esc(p.binding_version)} · ${esc(p.binding_state)} · rev ${p.profile_revision}</div></div>`;
    $('display-name').addEventListener('change', event => { try { editor.rename(event.target.value.trim()); render(); announce('角色名称已写入本地草稿；设备未改变。'); } catch (error) { event.target.value = p.display_name; announce(error.message, true); } });
    $('intent-grid').innerHTML = Object.entries(p.expression_map).map(([intent, semantic]) => `<div class="intent"><div class="from mono">${esc(intent)}</div><div class="to">${esc(names[semantic] || semantic)}</div><div class="muted small mono">${esc(semantic)} → ${esc(active().semantic_resources[semantic] || '未接线')}</div></div>`).join('');
    $('action-grid').innerHTML = Object.entries(p.action_map).map(([intent, action]) => `<div class="intent action-intent"><div class="from">角色动作意图（草稿）</div><div class="to mono">${esc(intent)} → ${esc(action)}</div><div class="muted small">仅为角色 profile 意图；不表示动作已映射到当前语义或已在设备执行。</div></div>`).join('') || '<div class="empty">此角色尚无动作意图草稿。</div>';
  }
  function renderSemanticList() {
    const query = $('semantic-search').value.trim().toLowerCase(), row = active(); $('semantic-list').replaceChildren();
    for (const [id, info] of Object.entries(D.semantics)) {
      if (query && !(id + ' ' + info.label + ' ' + row.semantic_resources[id]).toLowerCase().includes(query)) continue;
      const current = row.semantic_resources[id], changed = current !== info.resource_id, item = document.createElement('div'); item.className = 'semantic-row' + (changed ? ' changed' : '');
      const select = document.createElement('select'); select.dataset.semantic = id; const disconnected = document.createElement('option'); disconnected.value = ''; disconnected.textContent = '未接线'; disconnected.selected = current === null; select.append(disconnected); for (const resource of info.allowed_resources) { const option = document.createElement('option'); option.value = resource; option.textContent = resource; option.selected = resource === current; select.append(option); }
      item.innerHTML = `<div><div class="label">${esc(info.label)} <span class="mono muted">${esc(id)}</span></div><div class="details">动作：${esc(info.action_id || '无固定动作')} · 布局：${esc(info.layout_id)}</div></div>`; item.append(select); item.insertAdjacentHTML('beforeend', `<div class="baseline">E1.18 基线：<span class="mono">${esc(info.resource_id)}</span>${changed ? ` · 当前草稿：<span class="mono amber">${esc(current === null ? '未接线' : current)}</span>` : ' · 当前沿用基线'}</div>`); select.addEventListener('change', event => { try { selectedSemantic = id; const resource = event.target.value || null; editor.connect(id, resource); frame = 0; render(); announce(resource ? `${names[id]}：已在当前角色断开旧资源并接入 ${resource}；仅本地草稿。` : `${names[id]}：已在当前角色断开资源；仅本地草稿。`); } catch (error) { announce(error.message, true); render(); } }); item.addEventListener('click', event => { if (event.target === select) return; selectedSemantic = id; frame = 0; render(); }); $('semantic-list').append(item);
    }
  }
  function draw() {
    const canvas = $('preview'), ctx2 = canvas.getContext('2d'), resource = currentResource(), a = D.assets[resource];
    ctx2.clearRect(0, 0, canvas.width, canvas.height); ctx2.fillStyle = '#000'; ctx2.fillRect(0, 0, canvas.width, canvas.height); ctx2.save(); ctx2.beginPath(); ctx2.arc(180, 180, 180, 0, Math.PI * 2); ctx2.clip();
    if (!a) { ctx2.fillStyle = '#efbd70'; ctx2.textAlign = 'center'; ctx2.font = '16px system-ui'; ctx2.fillText('此语义尚未接入资源', 180, 180); ctx2.restore(); return; }
    if (image && imageResource === resource) {
      const n = a.frame_count || 1, i = Math.min(n - 1, Math.max(0, frame));
      const sx = (i % a.columns) * a.frame_width, sy = Math.floor(i / a.columns) * a.frame_height;
      const w = a.frame_width, h = a.frame_height, layout = D.semantics[selectedSemantic].layout_id;
      if (layout === 'eye_anim') {
        // Preserve the E1.19 reference composition: native pixels, two mirrored eyes.
        const cw = Math.min(w, 340), y = Math.floor((360 - h) / 2) + 10;
        ctx2.drawImage(image, sx, sy, cw, h, 10, y, cw, h);
        ctx2.translate(350, 0); ctx2.scale(-1, 1);
        ctx2.drawImage(image, sx, sy, cw, h, 0, y, cw, h);
      } else {
        const x = Math.floor((360 - w) / 2), y = layout === 'listen_anim' ? 25 : Math.floor((360 - h) / 2);
        ctx2.drawImage(image, sx, sy, w, h, x, y, w, h);
      }
    } else { ctx2.fillStyle = '#263238'; ctx2.fillRect(50, 50, 260, 260); ctx2.fillStyle = '#b8c4c6'; ctx2.textAlign = 'center'; ctx2.font = '16px system-ui'; ctx2.fillText('加载 EAF 图集…', 180, 184); } ctx2.restore();
  }
  function loadPreview() { const resource = currentResource(), a = D.assets[resource]; playing = false; $('play').textContent = '播放'; $('play').disabled = !a; $('step').disabled = !a; $('frame').disabled = !a; if (!a) { frame = 0; $('frame').max = '0'; $('frame').value = '0'; $('frame-value').textContent = '无资源'; image = null; imageResource = ''; draw(); return; } $('frame').max = String(Math.max(0, (a.frame_count || 1) - 1)); $('frame').value = String(Math.min(frame, Number($('frame').max))); frame = Number($('frame').value); if (imageResource !== resource) image = null; imageResource = resource; if (!a.atlas) { image = null; draw(); return; } const next = new Image(); next.onload = () => { if (currentResource() !== resource) return; image = next; draw(); }; next.onerror = () => { if (currentResource() !== resource) return; image = null; draw(); announce('官方图集载入失败：' + resource, true); }; next.src = a.atlas; }
  function renderGraph() { const info = D.semantics[selectedSemantic], current = currentResource(), a = D.assets[current], changed = current !== info.resource_id; $('selected-title').textContent = `${info.label} · ${selectedSemantic}`; $('graph').innerHTML = `<div class="graph-node"><div class="kind">角色意图 → 表情语义</div><div class="big">${esc(Object.entries(activeProfile().expression_map).filter(([, v]) => v === selectedSemantic).map(([k]) => k).join('、') || '未直接绑定')}</div><div class="muted small">${esc(roleId())} · 角色 profile 草稿</div></div><div class="arrow">→</div><div class="graph-node"><div class="kind">表情语义与 E1.18 路由</div><div class="big">${esc(info.label)}</div><div class="mono muted">${esc(selectedSemantic)}</div><div class="muted small">E1.18 基线伴随动作：${esc(info.action_id || '无')} · 布局：${esc(info.layout_id)}</div></div><div class="arrow">→</div><div class="graph-node ${changed ? 'changed' : 'current'}"><div class="kind">EAF 资源</div><div class="big mono">${esc(current === null ? '未接线' : current)}</div><div class="muted small">${changed ? '草稿新边 · E1.18 旧边已断开（仅本地）' : '沿用 E1.18 基线资源'}</div><div class="muted small">${a?.bytes || 0} B</div></div>`; loadPreview(); }
  function renderMeta() { const info = D.semantics[selectedSemantic], current = currentResource(), a = D.assets[current], changed = current !== info.resource_id; $('preview-meta').innerHTML = `<div class="metric"><span>角色</span><strong>${esc(activeProfile().display_name)}</strong></div><div class="metric"><span>语义</span><strong>${esc(info.label)} <span class="mono">${esc(selectedSemantic)}</span></strong></div><div class="metric"><span>资源</span><strong class="mono">${esc(current === null ? '未接线' : current)}</strong></div><div class="metric"><span>帧与计时</span><strong>${a ? `${a.frame_count} 帧 · ${info.fps} FPS · ${info.loop ? '循环' : '单次'}` : '无预览资源'}</strong></div><div class="metric"><span>映射状态</span><strong class="${changed ? 'amber' : 'green'}">${changed ? '本地草稿变更' : 'E1.18 基线'}</strong></div>`; $('frame-value').textContent = a ? `${frame} / ${Math.max(0, a.frame_count - 1)}` : '无资源'; }
  function renderChanges() { const changes = editor.changes(); $('changes').innerHTML = changes.length ? changes.map(x => `<div class="change"><strong>${esc(names[x.semantic] || x.semantic)}</strong><div class="danger mono">断开：${esc(x.from || '未接线')}</div><div class="green mono">接入：${esc(x.to || '未接线')}</div></div>`).join('') : '<div class="empty">当前角色尚无接线变化，全部沿用 E1.18 基线。</div>'; $('draft-badge').textContent = `草稿 ${changes.length} 项变化`; }
  function renderSharing() { const counts = editor.sharing(); $('sharing').innerHTML = Object.entries(counts).sort((a, b) => b[1].all_roles - a[1].all_roles || a[0].localeCompare(b[0])).map(([name, c]) => `<tr><td class="mono">${esc(name)}</td><td>${c.baseline}</td><td>${c.role}</td><td>${c.all_roles}</td><td>${(c.bytes / 1024).toFixed(1)} KiB</td></tr>`).join(''); }
  function renderHistory() { const rows = editor.history(); $('history').innerHTML = rows.length ? rows.map(row => `<div class="history-item"><div class="mono">#${row.sequence} · ${esc(row.operation)}</div><div class="muted small">${esc(typeof row.detail === 'string' ? row.detail : JSON.stringify(row.detail))}</div><div class="muted small">offline_draft_only</div></div>`).join('') : '<div class="empty">尚无操作；导入、接线、复制和撤销都会留下本地记录。</div>'; }
  function render() { renderRoles(); renderProfile(); renderSemanticList(); renderGraph(); renderMeta(); renderChanges(); renderSharing(); renderHistory(); $('undo').disabled = !editor.canUndo(); $('redo').disabled = !editor.canRedo(); }
  function download(name, text) { const url = URL.createObjectURL(new Blob([text], {type: 'application/json'})), link = document.createElement('a'); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  $('role-select').addEventListener('change', event => { editor.selectRole(event.target.value); selectedSemantic = 'happy'; frame = 0; render(); announce('已切换角色；草稿和 E1.18 基线仍分开显示。'); });
  $('clone').addEventListener('click', () => { try { const id = editor.clone(); selectedSemantic = 'happy'; frame = 0; render(); announce(`已复制角色 ${id}；新角色仍是 draft。`); } catch (error) { announce(error.message, true); } });
  $('restore').addEventListener('click', () => { try { editor.restoreRole(); render(); announce('当前角色的所有表情语义已恢复到 E1.18 基线；角色资料仍保留。'); } catch (error) { announce(error.message, true); } });
  $('undo').addEventListener('click', () => { if (editor.undo()) { render(); announce('已撤销最近一次本地草稿操作。'); } });
  $('redo').addEventListener('click', () => { if (editor.redo()) { render(); announce('已恢复最近一次本地草稿操作。'); } });
  $('export').addEventListener('click', () => { try { download('deskbot-e1-31-role-expression-draft.json', JSON.stringify(editor.exportDocument(), null, 2) + '\n'); announce('已导出 E1.31 draft JSON；它不是可刷写包。'); } catch (error) { announce(error.message, true); } });
  $('import-file').addEventListener('change', event => { const file = event.target.files && event.target.files[0]; if (!file) return; const reader = new FileReader(); reader.onload = () => { try { editor.importText(String(reader.result)); selectedSemantic = 'happy'; frame = 0; render(); announce('草稿已通过 schema、E1.18 基线和官方资源白名单校验并导入。'); } catch (error) { announce('导入被拒绝：' + error.message, true); } event.target.value = ''; }; reader.readAsText(file); });
  $('semantic-search').addEventListener('input', renderSemanticList);
  $('play').addEventListener('click', () => { playing = !playing; $('play').textContent = playing ? '暂停' : '播放'; if (playing) { last = performance.now(); if (!raf) raf = requestAnimationFrame(tick); } });
  $('step').addEventListener('click', () => { const a = D.assets[currentResource()]; if (!a) return; playing = false; $('play').textContent = '播放'; frame = (frame + 1) % Math.max(1, a.frame_count); $('frame').value = String(frame); renderMeta(); draw(); });
  $('frame').addEventListener('input', event => { playing = false; $('play').textContent = '播放'; frame = Number(event.target.value); renderMeta(); draw(); });
  function tick(now) { raf = 0; if (!playing || document.hidden) return; const info = D.semantics[selectedSemantic], a = D.assets[currentResource()]; if (!a) { playing = false; $('play').textContent = '播放'; return; } if (now - last > 1000 / Math.max(1, info.fps)) { frame = (frame + 1) % Math.max(1, a.frame_count); last = now; $('frame').value = String(frame); renderMeta(); draw(); } raf = requestAnimationFrame(tick); }
  document.addEventListener('visibilitychange', () => { if (document.hidden) { playing = false; $('play').textContent = '播放'; } });
  render(); announce('E1.31 已加载：E1.18 基线只读，本地角色草稿尚未部署。');
})();
