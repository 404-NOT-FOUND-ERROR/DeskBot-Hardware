(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('e129-data').textContent);
  const descriptions = {
    host: ['01 / HOST CAPTURE', '主机配对捕获', '主机在同一次工具往返中保存请求与响应，并记录接收时间。请求 ID 用来确认这两条消息属于同一次调用。', '校验目标：外层 MCP 消息齐全，请求与响应对应同一 JSON-RPC id。', 'capture'],
    mcp: ['02 / MCP ENVELOPE', 'MCP 外层消息', '小智传输把 JSON-RPC 放在 type 为 mcp 的外层消息中。请求和响应分别携带 payload；session_id 不进入导出结果。', '校验目标：两个 envelope 均为 mcp，payload 存在且是对象。', 'envelope'],
    rpc: ['03 / JSON-RPC', 'JSON-RPC 工具调用', '只接受 self.expression.observation 的 tools/call，并要求响应 JSON-RPC id 与请求完全匹配。', '校验目标：工具名、JSON-RPC 2.0、配对 id 和成功响应。', 'rpc'],
    text: ['04 / MCP TEXT', '嵌套文本快照', '工具响应只允许一个 text 块。导出器从 text 中解析 JSON，并严格验证 E1.26 快照 schema、语义白名单、记录上限和递增序号。', '校验目标：单一文本块、无重复 JSON 键、快照字段与序号有效。', 'text'],
    snapshot: ['05 / E1.26 → E1.27', '快照与来源证明', '验证成功后，E1.26 快照可直接导入 E1.27。provenance 记录请求 id、主机接收时间、哈希与丢弃计数，不保存原响应或 session_id。', '输出：observation-snapshot.json + provenance.json；只证明 SetEmotion 调用入口。', 'snapshot']
  };
  const elements = {
    stepLabel: document.getElementById('active-step'),
    title: document.getElementById('inspect-title'),
    description: document.getElementById('step-description'),
    label: document.getElementById('payload-label'),
    bytes: document.getElementById('payload-bytes'),
    payload: document.getElementById('payload'),
    note: document.getElementById('selection-note')
  };
  let step = 'host';
  let view = 'pair';
  const viewsForStep = {
    host: ['pair', 'request', 'response'],
    mcp: ['pair', 'request', 'response'],
    rpc: ['pair', 'request', 'response'],
    text: ['request', 'response'],
    snapshot: ['snapshot', 'provenance']
  };

  function valueFor() {
    const requestMessage = data.capture.request_message;
    const responseMessage = data.capture.response_message;
    const requestPayload = requestMessage.payload;
    const responsePayload = responseMessage.payload;
    const textBlock = responsePayload.result.content[0];
    if (step === 'snapshot') return view === 'provenance' ? data.sample_provenance : data.snapshot;
    if (step === 'host') {
      if (view === 'request') return requestMessage;
      if (view === 'response') return responseMessage;
      return data.capture;
    }
    if (step === 'mcp') {
      if (view === 'request') return requestMessage;
      if (view === 'response') return responseMessage;
      return {request_message: requestMessage, response_message: responseMessage};
    }
    if (step === 'rpc') {
      if (view === 'request') return requestPayload;
      if (view === 'response') return responsePayload;
      return {request: requestPayload, response: responsePayload};
    }
    if (step === 'text') {
      return view === 'request'
        ? {method: requestPayload.method, params: requestPayload.params}
        : textBlock;
    }
    return data.snapshot;
  }
  function render() {
    const copy = descriptions[step];
    document.querySelectorAll('.flow-node').forEach(node => {
      const active = node.dataset.step === step;
      node.classList.toggle('is-active', active);
      node.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('.switcher button').forEach(button => {
      const enabled = viewsForStep[step].includes(button.dataset.view);
      button.disabled = !enabled;
      button.setAttribute('aria-pressed', String(button.dataset.view === view));
    });
    elements.stepLabel.textContent = copy[0];
    elements.title.textContent = copy[1];
    elements.description.textContent = copy[2];
    elements.note.textContent = copy[3];
    const shown = valueFor();
    const text = JSON.stringify(shown, null, 2);
    elements.payload.textContent = text;
    elements.bytes.textContent = `${new TextEncoder().encode(text).length.toLocaleString()} bytes`;
    const viewLabel = ({pair: '成对消息', request: '请求层', response: '响应层', snapshot: '导入快照', provenance: '来源证明'})[view];
    elements.label.textContent = `${copy[4]} · ${viewLabel} · 合成内容`;
    document.body.dataset.ready = 'true';
  }

  document.querySelectorAll('.flow-node').forEach(node => node.addEventListener('click', () => {
    step = node.dataset.step;
    view = step === 'snapshot' ? 'snapshot' : step === 'text' ? 'response' : 'pair';
    render();
  }));
  document.querySelectorAll('.switcher button').forEach(button => button.addEventListener('click', () => {
    view = button.dataset.view;
    render();
  }));
  render();
})();
