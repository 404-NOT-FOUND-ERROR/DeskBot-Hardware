import {timingSafeEqual} from 'node:crypto';
import {isIPv4} from 'node:net';
import {networkInterfaces} from 'node:os';

const deviceMacPattern = /^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const digestPattern = /^[0-9a-f]{64}$/;
const ipNumber = address => address.split('.').reduce((value, octet) => (value << 8) | Number(octet), 0);
const privateAddress = address => {
  if (!isIPv4(address)) return false;
  const [a, b] = address.split('.').map(Number);
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
};

export function validateTrialDevice(config) {
  if (!config || typeof config !== 'object' || Array.isArray(config)
      || !deviceMacPattern.test(config.device_mac) || !uuidPattern.test(config.client_uuid)
      || !digestPattern.test(config.firmware_elf_sha256)) throw new Error('invalid_voice_trial_identity');
}

export function trustedTrialNetwork(host, interfaces = networkInterfaces()) {
  if (!privateAddress(host)) throw new Error('voice_trial_requires_private_ipv4');
  const adapter = Object.values(interfaces).flat().find(item =>
    item?.family === 'IPv4' && !item.internal && item.address === host && isIPv4(item.netmask));
  if (!adapter) throw new Error('voice_trial_address_not_on_this_computer');
  const mask = ipNumber(adapter.netmask);
  if (mask === 0) throw new Error('voice_trial_requires_local_subnet');
  const subnet = ipNumber(host) & mask;
  return address => privateAddress(address) && (ipNumber(address) & mask) === subnet;
}

function reply(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {'content-type':'application/json', 'content-length':body.length,
    'cache-control':'no-store', connection:'close'});
  response.end(body);
}

export function createDeviceVoiceTrial({config, token, setupKey, allowPeer}) {
  validateTrialDevice(config);
  if (!digestPattern.test(token) || !/^[A-Za-z0-9_-]{24}$/.test(setupKey) || typeof allowPeer !== 'function')
    throw new Error('invalid_voice_trial_setup');

  const {device_mac, client_uuid, firmware_elf_sha256} = config;
  const path = '/deskbot-voice/' + setupKey;
  let lastCheck = {state:'waiting', at:null};
  const update = state => {lastCheck = {state, at:new Date().toISOString()};};
  const address = request => `${config.host}:${request.socket.localPort}`;
  const allows = (request, upgrade = false) => allowPeer(request.socket.remoteAddress)
    && request.headers.origin === undefined && (request.headers.host === address(request)
      // The ESP WebSocket client omits the port; its HTTP client includes it.
      || (upgrade && request.headers.host === config.host));
  const correctDevice = request => request.headers['device-id'] === device_mac
    && request.headers['client-id'] === client_uuid;
  const route = Buffer.from(path);

  async function handleRequest(request, response) {
    // The OTA request contains device information; validate it and keep only the outcome.
    const incoming = Buffer.from(request.url ?? '');
    if (!allows(request) || request.method !== 'POST' || incoming.length !== route.length
        || !timingSafeEqual(incoming, route)) return reply(response,404,{error:'not_found'});
    if (!correctDevice(request)) return reply(response,403,{error:'device_not_registered'});
    const size = Number(request.headers['content-length']);
    if (!Number.isInteger(size) || size < 1 || size > 8192) return reply(response,400,{error:'invalid_device_info'});
    let info;
    try {
      const chunks = [];
      let bytes = 0;
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > size) throw new Error('invalid_device_info_length');
        chunks.push(chunk);
      }
      if (bytes !== size) throw new Error('incomplete_device_info');
      info = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      if (!response.destroyed) reply(response,400,{error:'invalid_device_info'});
      return;
    }
    if (info?.chip_model_name !== 'esp32s3' || info.mac_address !== device_mac || info.uuid !== client_uuid
        || info.application?.elf_sha256 !== firmware_elf_sha256) {
      update('firmware_mismatch');
      return reply(response,409,{error:'expected_firmware_required'});
    }
    const websocket = {url:`ws://${address(request)}/xiaozhi/v1`, token, version:1};
    update('configured');
    // A WebSocket-only response makes this firmware choose it ahead of MQTT stored from an earlier boot.
    reply(response,200,{websocket});
  }

  return {path, allowsUpgrade:request => allows(request,true), handleRequest, status: () => ({...lastCheck})};
}
