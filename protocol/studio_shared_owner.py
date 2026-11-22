"""A workbench page reuses a running local USB owner through its existing API."""
import json
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

from protocol.magnetic_stream import MagneticStream
from protocol.studio_control_bridge import validate_control
from protocol.studio_control_link import validate_command


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SharedWorkbenchBridge:
    def __init__(self, url, device_id):
        parts=urlsplit(url)
        if (parts.scheme!='http' or parts.hostname!='127.0.0.1' or not parts.port
                or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment):
            raise ValueError('共享连接需要明确的本机工作台地址')
        self.url=url.rstrip('/');self.device_id=device_id
        self.lock=threading.RLock()
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def request(self, route, value=None):
        headers={'Accept':'application/json'}
        raw=None
        if value is not None:
            raw=json.dumps(value).encode('utf-8')
            headers.update({'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'})
        try:
            with self.opener.open(Request(self.url+route,data=raw,headers=headers),timeout=5) as response:
                raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError('原工作台状态超过大小限制')
                return json.loads(raw)
        except HTTPError as error:
            raise OSError('原工作台请求未完成，请重新读取状态') from error
        except (URLError,TimeoutError) as error:
            raise OSError('正在等待原工作台连接') from error

    def control(self):
        with self.lock:
            value=self.request('/api/studio/state')
            if not self.valid(value):raise ValueError('原工作台控制状态格式不匹配')
            if value['target_id'] is not None and value['target_id']!=self.device_id:
                raise ValueError('原工作台连接的桌宠已变化')
            return value

    @staticmethod
    def valid(value):
        return type(value) is dict and validate_control({k:v for k,v in value.items() if k!='host_command'})

    def command(self,value):
        with self.lock:
            value=validate_command(value)
            current=self.control()
            if any(value[key]!=current[key] for key in ('connection_id','target_id','boot_id','session_id')):
                raise ValueError('连接已更新，请重新读取')
            if value['operation'].startswith('role.activate.'):
                automatic=self.request('/api/shell-runtime/state')
                if automatic.get('enabled') is not False:
                    raise ValueError('请先在原工作台暂停自动换角，再确认安装')
            # The original owner's coordinator still serializes client and installation requests.
            result=self.request('/api/studio/command',value)
            if not self.valid(result):raise OSError('原工作台结果待查询确认')
            return result

    def snapshot(self):
        with self.lock:return self.request('/api/body/state')

    def shell_inputs(self):
        with self.lock:
            control=self.control()
            return dict(control=control,magnetic=MagneticStream().snapshot(control),motion=None)

    def ports(self):
        return dict(available=False,reason='shared_owner_connection',ports=[])

    def connect(self,*args):
        raise ValueError('串口连接由原工作台管理')

    def disconnect(self):
        raise ValueError('串口连接由原工作台管理')

    def close(self):
        # Closing a secondary page preserves the original USB connection and client service.
        pass
