"""Same-origin routes for real expression uploads, builds and installation."""
import json
from urllib.parse import urlsplit,parse_qs,urlencode
from protocol.layered_face import ROOT,sha


class ExpressionRoutes:
    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/expressions'):
            if not self.allowed():return
            names={'/expressions':'expression-pipeline.html','/expressions/':'expression-pipeline.html',
                '/expressions/app.js':'expression-pipeline.js','/expressions/style.css':'expression-pipeline.css'}
            if path not in names:return self.json(404,dict(error='文件不存在'))
            f=ROOT/'scripts'/names[path]
            return self.reply(200,f.read_bytes(),{'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css'}[f.suffix])
        if not path.startswith('/api/expressions/'):return super().do_GET()
        if not self.allowed():return
        store=self.server.expression_projects
        if store is None:return self.json(503,dict(error='请指定当前已安装的分层资源包'))
        try:
            if path=='/api/expressions/state':return self.json(200,store.state())
            q=parse_qs(urlsplit(self.path).query,strict_parsing=True)
            ident=q['project'][0];folder=store.folder(ident);p=store.load(ident)
            if path=='/api/expressions/file':
                revision=int(q['revision'][0]);name=q['name'][0]
                allowed={'index.html','preview.js','device.js','face-motion.dfm','assets-8m.bin','xiaozhi.bin',
                    'deskbot-expression.zip','delivery.json','report.json','deskbot-roles.json'}
                build=p.get('build')
                if (name not in allowed or not build or build.get('status')!='ready'
                        or revision!=build['revision'] or revision!=p['revision']):raise ValueError('产物尚未完成生成')
                f=folder/('build-'+str(revision))/name
                if sha(f.read_bytes())!=build.get('files',{}).get(name):raise ValueError('产物摘要已变化，请重新生成')
            elif path=='/api/expressions/source':
                asset=q['asset'][0]
                if asset not in [a['id'] for a in p['assets']]:raise ValueError('素材不存在')
                name=asset+'.gif';f=folder/'sources'/name
            else:return self.json(404,dict(error='接口不存在'))
            mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8',
                '.json':'application/json','.gif':'image/gif'}.get(f.suffix,'application/octet-stream')
            if name=='index.html':
                text=f.read_text('utf-8')
                for n in ('preview.js','device.js'):
                    text=text.replace('src="'+n+'"','src="/api/expressions/file?'+urlencode(dict(project=ident,revision=revision,name=n))+'"')
                return self.reply(200,text.encode(),mime)
            return self.reply(200,f.read_bytes(),mime,filename=name if name.endswith(('.zip','.bin','.dfm')) else None)
        except (ValueError,KeyError,OSError):return self.json(404,dict(error='此项目或产物尚未生成'))

    def do_POST(self):
        path=urlsplit(self.path).path
        if not path.startswith('/api/expressions/'):return super().do_POST()
        if not self.allowed():return
        if self.headers.get('X-DeskBot-Studio')!='expression-pipeline-v1' or self.headers.get('Transfer-Encoding'):
            return self.json(415,dict(error='请从表情工作台操作'))
        store=self.server.expression_projects
        if store is None:return self.json(503,dict(error='请先登记当前分层资源包'))
        try:
            lengths=self.headers.get_all('Content-Length',[]);size=int(lengths[0]) if len(lengths)==1 else -1
            if not 0<size<=(32*1024*1024 if path.endswith('/upload') else 8192):raise ValueError('提交大小超出范围')
            self.connection.settimeout(30);raw=self.rfile.read(size)
            if len(raw)!=size:raise ValueError('素材尚未完整接收')
            if path.endswith('/upload'):
                q=parse_qs(urlsplit(self.path).query,strict_parsing=True)
                result=store.upload(q['project'][0],int(q['revision'][0]),q['name'][0],raw)
            else:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('请提交 JSON 参数')
                from protocol.studio_usb_session import _unique
                v=json.loads(raw,object_pairs_hook=_unique);action=path.rsplit('/',1)[1]
                if action=='create':result=store.create(**v)
                elif action=='bind':result=store.bind(**v)
                elif action=='build':result=store.start_build(**v)
                elif action=='install':result=store.start_install(bridge=self.server.bridge,**v)
                elif action=='recover':result=store.start_recovery(bridge=self.server.bridge,**v)
                elif action=='rollback':result=store.start_rollback(bridge=self.server.bridge,**v)
                else:raise ValueError('操作不存在')
            return self.json(200,result)
        except (ValueError,KeyError,TypeError,OSError,EOFError) as error:return self.json(422,dict(error=str(error)))
