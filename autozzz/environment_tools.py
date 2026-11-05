"""Bounded package-index probes and explicit proxy discovery, without game input."""
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
import hashlib
import re
import threading
import time
from urllib.parse import urljoin,urlsplit,unquote,urldefrag
import urllib.request
import uuid

from .onedragon_environment import EnvironmentStore,validate_url

INDEXES=('https://pypi.org/simple','https://pypi.tuna.tsinghua.edu.cn/simple','https://mirrors.aliyun.com/pypi/simple')
PROXY_DISCOVERY='https://ghproxy.link/js/src_views_home_HomeView_vue.js'


def safe_https(address,hosts):
    parts=urlsplit(address)
    if (parts.scheme!='https' or parts.hostname not in hosts or parts.username is not None or parts.password is not None
            or parts.port not in (None,443) or parts.fragment or any(c.isspace() or ord(c)<32 for c in address)):
        raise ValueError('下载地址或重定向不在允许来源中')
    return address


def read_network(address,cancelled,*,proxy=None,limit=2*1024**2,allowed_hosts=None,seconds=15):
    hosts=set(allowed_hosts or [urlsplit(address).hostname]);safe_https(address,hosts)
    if proxy:validate_url(proxy,proxy=True)
    if proxy and not proxy.startswith(('http://','https://')):
        raise ValueError('此下载通道需要 HTTP(S) 个人代理；当前 SOCKS 设置未被静默忽略')
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,request,file,code,message,headers,newurl):
            if cancelled.is_set():raise InterruptedError('环境网络操作已取消')
            if time.monotonic()>deadline:raise TimeoutError('网络读取超时')
            safe_https(newurl,hosts)
            return super().redirect_request(request,file,code,message,headers,newurl)
    # Ignore ambient proxy credentials/configuration; use only the explicitly configured proxy.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy} if proxy else {}),Redirect())
    if cancelled.is_set():raise InterruptedError('环境网络操作已取消')
    deadline=time.monotonic()+seconds;content=bytearray()
    try:
        request=urllib.request.Request(address,headers={'User-Agent':'AutoZZZ-Environment/1','Accept':'text/html,application/octet-stream'})
        with opener.open(request,timeout=5) as response:
            safe_https(response.geturl(),hosts)
            while True:
                if cancelled.is_set():raise InterruptedError('环境网络操作已取消')
                if time.monotonic()>deadline:raise TimeoutError('网络读取超时')
                chunk=response.read(65536)
                if not chunk:break
                content.extend(chunk)
                if len(content)>limit:raise ValueError('网络响应超过允许大小')
    except InterruptedError:raise
    except ValueError:raise
    except Exception as exc:
        # Proxy credentials and request internals must never enter RPC diagnostics.
        raise RuntimeError('网络请求失败（'+type(exc).__name__+'）') from None
    return bytes(content)


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            value=dict(attrs).get('href')
            if value:self.links.append(value)


def resolve_pinned_archive(index,filename,sha256,cancelled,*,proxy=None):
    """Select one exact archive from the selected fixed index; never runs pip/setup.py."""
    index=index.rstrip('/') if isinstance(index,str) else index
    if index not in INDEXES:raise ValueError('依赖下载源不在固定上游支持列表中')
    if re.fullmatch(r'[A-Za-z0-9_.-]+\.tar\.gz',filename) is None or re.fullmatch('[0-9a-f]{64}',sha256) is None:
        raise ValueError('固定依赖归档身份无效')
    package=filename.split('-',1)[0];page=index+'/'+package+'/'
    hosts={urlsplit(index).hostname}
    if index==INDEXES[0]:hosts.add('files.pythonhosted.org')
    raw=read_network(page,cancelled,proxy=proxy,allowed_hosts=hosts)
    links=Links();links.feed(raw.decode('utf-8',errors='replace'));matches=set()
    for link in links.links:
        address=urljoin(page,unescape(link));parts=urlsplit(address)
        if unquote(parts.path.rsplit('/',1)[-1])!=filename:continue
        if parts.fragment and parts.fragment!='sha256='+sha256:continue
        address=urldefrag(address)[0];safe_https(address,hosts);matches.add(address)
    if len(matches)!=1:raise ValueError('选定依赖源未提供唯一的固定版本归档；未自动切换下载源')
    return matches.pop(),hosts


def dependency_network():
    """Read real SDK config without constructing a context/controller or opening a device."""
    from one_dragon.base.config.yaml_operator import cached_yaml_data
    from one_dragon.envs.repo_config import RepoConfig
    from one_dragon.envs.env_config import EnvConfig
    cached_yaml_data.clear();repo=RepoConfig(prefer_bundled_config=True);config=EnvConfig(repo)
    index=config.pip_source.rstrip('/')
    if index not in INDEXES or index not in {item.value.rstrip('/') for item in repo.get_source_values('pip_source')}:
        raise ValueError('Python 依赖下载源无效，请重新选择')
    proxy=config.personal_proxy if config.is_personal_proxy else None
    if proxy:validate_url(proxy,proxy=True)
    return index,proxy


def proxy_candidate(raw):
    # Parse the upstream JS text only; never execute downloaded JavaScript.
    text=re.sub(r'\\+(["\'])',r'\1',raw.decode('utf-8',errors='replace'))
    links=re.findall(r'<a\s+href=["\']([^"\']+)["\']',text)
    if len(links)!=1:raise ValueError('代理发现页面结构已变化；未修改现有配置')
    address=unescape(links[0]);parts=urlsplit(address)
    if (re.fullmatch(r'https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}',address) is None
            or parts.hostname.endswith(('.localhost','.local')) or '..' in parts.hostname):
        raise ValueError('发现的代理地址无效；未修改现有配置')
    validate_url(address);return address


class EnvironmentTools:
    def __init__(self,service):
        self.service=service;self.worker=None;self.lock=threading.RLock();self.cancelled=threading.Event()
        self.job={};self.closed=False

    @property
    def busy(self):return self.worker is not None and self.worker.is_alive()

    def catalog(self):
        snapshot=EnvironmentStore(self.service).describe('_environment')
        fields={field['name']:field for field in snapshot['fields']}
        return dict(revision=snapshot['revision'],pip_source=fields['pip_source']['value'],
            options=fields['pip_source']['options'],proxy_configured=fields['gh_proxy_url']['configured'])

    def start(self,action,revision):
        if action not in ('pip_test','proxy_discover'):raise ValueError('未知环境工具操作')
        current=self.catalog()
        if revision!=current['revision']:raise ValueError('环境配置已改变，请刷新后重试')
        config=EnvironmentStore(self.service).config('_environment')
        proxy=config.personal_proxy if config.is_personal_proxy else None
        if proxy:validate_url(proxy,proxy=True)
        with self.lock:
            if self.closed:raise RuntimeError('环境工具连接已关闭')
            if self.busy:raise RuntimeError('环境工具仍在运行')
            self.cancelled.clear();self.job=dict(job_id=uuid.uuid4().hex,action=action,state='working',
                detail='正在请求选定来源',revision=revision,results=[],candidate='',applied=False)
            self.worker=threading.Thread(target=self._work,args=(action,current['options'],proxy),name='AutoZZZ-EnvironmentTools',daemon=False)
            self.worker.start()
        return self.status()

    def _work(self,action,options,proxy):
        try:
            if action=='proxy_discover':
                raw=read_network(PROXY_DISCOVERY,self.cancelled,proxy=proxy)
                candidate=proxy_candidate(raw);rows=[dict(label='发现的 GitHub 代理',value=candidate,elapsed_ms=None,error='')]
            else:
                rows=[]
                for item in options:
                    if self.cancelled.is_set():raise InterruptedError('环境网络操作已取消')
                    value=item['value'].rstrip('/');start=time.monotonic();error=''
                    try:
                        from .gamepad_install import SHA256
                        resolve_pinned_archive(value,'vgamepad-0.1.0.tar.gz',SHA256,self.cancelled,proxy=proxy)
                    except InterruptedError:raise
                    except Exception as exc:error=str(exc)
                    rows.append(dict(label=item['label'],value=value,elapsed_ms=round((time.monotonic()-start)*1000),error=error))
                    with self.lock:self.job['results']=deepcopy(rows)
                available=[row for row in rows if not row['error']]
                if not available:raise ValueError('全部依赖源响应测试失败；未修改现有配置')
                candidate=min(available,key=lambda row:row['elapsed_ms'])['value']
            with self.lock:
                if self.cancelled.is_set():raise InterruptedError('环境网络操作已取消')
                self.job.update(state='success',detail='检查完成；点击应用才修改配置',results=rows,candidate=candidate)
        except Exception as exc:
            with self.lock:self.job.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed',detail=str(exc))

    def status(self,job_id=None):
        with self.lock:
            if job_id is not None and job_id!=self.job.get('job_id'):raise ValueError('环境工具任务编号已改变')
            return dict(deepcopy(self.job),busy=self.busy)

    def apply(self,job_id,value,revision):
        self.service._assert_idle()
        with self.lock:
            job=deepcopy(self.job)
            if self.closed or self.busy or job.get('job_id')!=job_id or job.get('state')!='success':
                raise ValueError('环境工具结果不可应用，请重新检查')
            if revision!=job['revision']:raise ValueError('环境配置版本不符')
            if value not in {row['value'] for row in job['results'] if not row['error']}:
                raise ValueError('只能应用本轮成功检查的候选')
            key='pip_source' if job['action']=='pip_test' else 'gh_proxy_url'
            EnvironmentStore(self.service).save('_environment',{key:value},revision)
            self.job['applied']=True
        return self.catalog()

    def cancel(self,job_id):
        with self.lock:
            if job_id!=self.job.get('job_id'):raise ValueError('环境工具任务编号已改变')
            self.cancelled.set()
        return self.status(job_id)

    def close(self):
        with self.lock:self.closed=True;self.cancelled.set();worker=self.worker
        if worker is not None:worker.join()
