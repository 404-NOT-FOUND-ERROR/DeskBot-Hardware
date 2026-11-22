import {createServer} from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {WebSocketServer} from './runtime.mjs';
import {NetworkSession,PacedPeer} from './network-session.mjs';
import {requireThat} from '../client-4b2a/xiaozhi-codec.mjs';

const CONTROL_LIMIT=8192;
function tokenMatches(actual,expected){
  if(typeof actual!=='string'||actual.length>512)return false;
  const a=Buffer.from(actual),b=Buffer.from(`Bearer ${expected}`);
  return a.length===b.length&&timingSafeEqual(a,b);
}
export function createVoiceGateway({binding,client,voice,token,deviceHeader,clientHeader,
  onOutcome=()=>{},sessionOptions={},deviceTrial=null}){
  requireThat(typeof token==='string'&&token.length>=24&&token.length<=256,'device_token_required');
  requireThat(typeof deviceHeader==='string'&&deviceHeader.length>0&&typeof clientHeader==='string'&&clientHeader.length>0,
    'device_headers_required');
  const websocket=new WebSocketServer({noServer:true,maxPayload:CONTROL_LIMIT,perMessageDeflate:false});
  let active=null,upgrades=0;const sessions=new Set();
  const server=createServer(deviceTrial ? {requestTimeout:5000,headersTimeout:5000,
    connectionsCheckingInterval:1000,keepAliveTimeout:1000} : {}, (request,response)=>{
    if(deviceTrial){
      void deviceTrial.handleRequest(request,response).catch(()=>response.destroy());
      return;
    }
    const body=JSON.stringify(request.url==='/health'?{status:'ok',mode:'local_voice_evaluation',
      device_connected:active!==null,voice_provider:'windows-system-speech',
      real_audio:true,device_migrated:false}: {error:'not_found'});
    response.writeHead(request.url==='/health'?200:404,{'content-type':'application/json','content-length':Buffer.byteLength(body)});
    response.end(body);
  });
  server.on('upgrade',(request,socket,head)=>{
    socket.on('error',()=>{});
    const reject=status=>socket.end(`HTTP/1.1 ${status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    if(request.url!=='/xiaozhi/v1'){reject('404 Not Found');return;}
    if(deviceTrial&&!deviceTrial.allowsUpgrade(request)){reject('403 Forbidden');return;}
    if(!tokenMatches(request.headers.authorization,token)||request.headers['device-id']!==deviceHeader
      ||request.headers['client-id']!==clientHeader){reject('401 Unauthorized');return;}
    const version=Number(request.headers['protocol-version']);
    if(!(deviceTrial?[1]:[1,2,3]).includes(version)){reject('400 Bad Request');return;}
    // One active connection and at most one old settling session per instance.
    if(sessions.size>=2){reject('503 Service Unavailable');return;}
    websocket.handleUpgrade(request,socket,head,ws=>{
      const previous=active;
      if(previous){previous.session.close();previous.ws.close(4000,'connection_replaced');}
      // Let the ESP main loop enter Speaking before its first queued Opus frame.
      const peer=new PacedPeer(ws,{firstFrameLeadMs:deviceTrial?120:0});
      const session=new NetworkSession({binding,client,recognize:value=>voice.recognize(value),
        synthesize:value=>voice.synthesize(value),peer,...sessionOptions,
        onOutcome:result=>onOutcome(result)});
      const current={ws,session};active=current;sessions.add(current);upgrades++;
      ws.on('error',()=>{});
      ws.on('message',(bytes,binary)=>{
        if(session.state==='closed'||ws.readyState!==1)return;
        try{
          if(binary){session.receiveAudio(bytes);return;}
          requireThat(bytes.length<=CONTROL_LIMIT,'control_too_large');
          const message=JSON.parse(bytes.toString('utf8'));
          if(session.state==='awaiting_hello')requireThat(message.version===version,'protocol_version_mismatch');
          session.receiveJson(message);
        }catch(error){
          onOutcome({status:error.code??'invalid_control',session_id:session.sessionId});
          session.close();ws.close(1008,'voice_protocol_error');
        }
      });
      ws.once('close',()=>{
        session.close();if(active===current)active=null;
        session.drain().finally(()=>sessions.delete(current));
      });
    });
  });
  return {server,
    snapshot:()=>({connected:active!==null,sessions:sessions.size,upgrades,
      session:active?.session.snapshot()??null}),
    async close(){
      for(const item of sessions){item.session.close();item.ws.terminate();}
      await Promise.all([...sessions].map(item=>item.session.drain()));
      await new Promise(resolve=>websocket.close(resolve));
      if(server.listening)await new Promise(resolve=>server.close(resolve));
    },
  };
}
