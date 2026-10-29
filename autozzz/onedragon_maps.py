"""实际地图快照的草稿编辑、道路拼接和关联路线坐标迁移。"""
from __future__ import annotations

import base64
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid

from .map_transaction import MapTransaction, fingerprint
from .onedragon_library import LibraryStore
from .onedragon_library_rules import coordinate, data_tree
from .run_lock import profile_lock

MAP_KIND = "world_patrol_map"
MAX_PIXELS = 32_000_000


def scope_revision(values):
    digest=hashlib.sha256()
    for key,raw in sorted(values.items()):digest.update(key.encode('utf-8')+b'\0'+fingerprint(raw).encode()+b'\0')
    return digest.hexdigest()


def png_preview(image):
    if image is None:return ""
    import cv2
    height,width=image.shape[:2];ratio=min(1.,1200/max(width,height))
    image=cv2.resize(image,(max(1,round(width*ratio)),max(1,round(height*ratio))),interpolation=cv2.INTER_NEAREST)
    ok,png=cv2.imencode('.png',image)
    if not ok or png.nbytes>768*1024:raise ValueError("地图预览过大。")
    return base64.b64encode(png).decode('ascii')


def read_mini(ctx, image, threshold):
    from zzz_od.application.devtools.large_map_recorder import large_map_recorder_utils as utils
    if ctx.screen_loader.get_area('大世界','小地图') is None:ctx.screen_loader.reload()
    wrapper=ctx.world_patrol_service.cut_mini_map(image)
    if not wrapper.play_mask_found:raise ValueError("未识别到大世界小地图的玩家标记。")
    import cv2
    raw=cv2.cvtColor(wrapper.rgb,cv2.COLOR_RGB2BGR)
    ratio=min(1.,256/max(raw.shape[:2]))
    raw=cv2.resize(raw,(max(1,round(raw.shape[1]*ratio)),max(1,round(raw.shape[0]*ratio))))
    return utils.create_mini_map_snapshot(ctx,wrapper,threshold),png_preview(raw)


def mini_snapshot(ctx,image,threshold):return read_mini(ctx,image,threshold)[0]


def snapshot_payload(snapshot, warning="", views=None):
    import cv2
    ok,png=cv2.imencode('.png',snapshot.road_mask)
    if not ok or png.nbytes>512*1024:raise ValueError("小地图快照无法编码。")
    return dict(mask=base64.b64encode(png).decode('ascii'),icons=[dict(template_id=name,point=[int(point.x),int(point.y)]) for name,point in snapshot.icon_list],warning=warning,views=views or [])


class MapStore:
    def __init__(self,service):
        self.service=service;self.library=LibraryStore(service)
        self.root=Path(service.data).resolve();self.ctx=service.ctx;self.tx=MapTransaction(self.root)
        self.state=None;self.undo=None;self.base_icons={}
        self.preferences=dict(threshold=.7,use_icons=True)

    def _area(self,key):
        if not isinstance(key,str) or key not in self.library.areas:raise ValueError("请选择有效地图。")
        return self.library.areas[key]

    def _paths(self,key):
        from zzz_od.application.world_patrol.world_patrol_area import road_mask_path,icon_yaml_path
        area=self._area(key)
        road=self.tx.target(Path(road_mask_path(area)).relative_to(self.root).as_posix())
        icons=self.tx.target(Path(icon_yaml_path(area)).relative_to(self.root).as_posix())
        directory=self.tx.inside(self.root/'config/world_patrol_route/system'/area.entry.entry_id/area.full_id)
        return road,icons,sorted(self.tx.target(path.relative_to(self.root).as_posix()) for path in directory.glob('*.yml'))

    def _scope(self,key):
        road,icons,routes=self._paths(key)
        return {path.relative_to(self.root).as_posix():self.tx.read(path) for path in (road,icons,*routes)}

    def revision(self,key):return scope_revision(self._scope(key))

    def catalog(self):
        return [dict(kind=MAP_KIND,key=a.full_id,name=a.entry.entry_name+' · '+a.full_name,short_name=a.full_name,area_id=a.full_id,
                     entry_id=a.entry.entry_id,entry_name=a.entry.entry_name,builtin=False,overridden=False)
                for a in self.library.areas.values()]

    def _check_size(self,width,height):
        if not 1<=width<=16384 or not 1<=height<=16384 or width*height>MAX_PIXELS:raise ValueError("地图宽高须为 1—16384 像素，总像素不能超过 3200 万。")

    def read(self,key):
        self.service._assert_idle()
        with profile_lock(self.root):return self._read(key)

    def _read(self,key):
        self.tx.assert_clear();area=self._area(key)
        from one_dragon.utils import cv2_utils,yaml_utils
        from zzz_od.application.world_patrol.world_patrol_area import WorldPatrolLargeMap,WorldPatrolLargeMapIcon
        from one_dragon.base.geometry.point import Point
        road,icons,routes=self._paths(key);image=None;rows=[]
        if road.is_file():
            from PIL import Image
            with Image.open(road) as header:self._check_size(*header.size)
            image=cv2_utils.read_image(str(road))
            if image is None:raise ValueError("地图图片无法读取。")
            import cv2
            if image.ndim==3:image=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
            if icons.is_file():
                raw=yaml_utils.safe_load(self.tx.read(icons));data_tree(raw)
                if not isinstance(raw,list):raise ValueError("地图图标须为列表。")
                rows=[WorldPatrolLargeMapIcon(**row) for row in raw]
        large=WorldPatrolLargeMap(key,image,rows)
        ids=[uuid.uuid4().hex for _ in rows]
        self.base_icons={identifier:icon.to_dict() for identifier,icon in zip(ids,rows)}
        self.state=dict(id=uuid.uuid4().hex,key=key,version=1,revision=self.revision(key),large=large,ids=ids,
                        anchor=None if image is None else Point(image.shape[1]//2,image.shape[0]//2),mini=None,views=[],match=None,
                        transform=[1.,1.,0.,0.],steps=[],base_size=None if image is None else [image.shape[1],image.shape[0]],
                        dirty=False,threshold=self.preferences['threshold'],use_icons=self.preferences['use_icons'],warning='',base_missing=image is None)
        self.undo=None
        return self._document()

    def _current(self,data):
        if not isinstance(data,dict) or set(data)!={'draft_id','version'} or self.state is None or data['draft_id']!=self.state['id'] or type(data['version'])is not int or data['version']!=self.state['version']:
            raise ValueError("地图草稿已变化，请重新读取。")
        return self.state

    def _document(self):
        state=self.state;large=state['large'];area=self._area(state['key']);image=large.road_mask
        height,width=(0,0) if image is None else image.shape[:2]
        rows=[dict(icon.to_dict(),id=identifier) for identifier,icon in zip(state['ids'],large.icon_list)]
        match=state['match'];anchor=state['anchor']
        templates=[]
        for i in range(1,100):
            identifier=f'map_icon_{i:02d}'
            if self.ctx.template_loader.get_template('map',identifier) is None:break
            templates.append(dict(value=identifier,label='传送点' if i==1 else f'图标 {i}'))
        return dict(kind=MAP_KIND,key=state['key'],name=area.entry.entry_name+' · '+area.full_name,revision=state['revision'],builtin=False,overridden=False,
                    draft=state['dirty'],data=dict(draft_id=state['id'],version=state['version']),fields=[],
                    preview=dict(width=width,height=height,png=png_preview(image),icons=rows,templates=templates,
                                 anchor=None if anchor is None else [anchor.x,anchor.y],
                                 candidate=None if match is None else [match.center.x,match.center.y],
                                 match_value=None if match is None else float(match.confidence),
                                 mini_png='' if state['mini'] is None else png_preview(state['mini'].road_mask),
                                 views=list(state['views']),
                                 undo=self.undo is not None,threshold=state['threshold'],use_icons=state['use_icons'],warning=state['warning'],
                                 transform=state['transform'],route_count=len(self._paths(state['key'])[2])))

    def _point(self,values,width,height):
        from one_dragon.base.geometry.point import Point
        if not isinstance(values,list) or len(values)!=2:raise ValueError("位置需要两个整数坐标。")
        return Point(coordinate(values[0],width),coordinate(values[1],height))

    def _icons(self,rows,strict=False):
        from zzz_od.application.world_patrol.world_patrol_area import WorldPatrolLargeMapIcon
        state=self.state;image=state['large'].road_mask
        if image is None:raise ValueError("请先建立地图。")
        height,width=image.shape[:2]
        if not isinstance(rows,list) or len(rows)>500:raise ValueError("地图最多包含 500 个图标。")
        ids=[];icons=[];names=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'id','icon_name','template_id','lm_pos','tp_pos'}:raise ValueError("图标字段无效。")
            identifier=row['id'];name=row['icon_name'];template=row['template_id']
            if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier) or identifier in ids:raise ValueError("图标编号无效或重复。")
            if not isinstance(name,str) or name!=name.strip() or len(name)>100 or any(ord(c)<32 for c in name):raise ValueError("图标名称须为不含控制字符的 0—100 字文本。")
            if not isinstance(template,str) or not re.fullmatch('map_icon_[0-9]{2}',template) or self.ctx.template_loader.get_template('map',template) is None:raise ValueError("图标类型不存在。")
            if template=='map_icon_01' and name:
                if name in names:raise ValueError("传送点名称不能重复。")
                names.add(name)
            if strict and template=='map_icon_01' and not name:raise ValueError("请为每个传送点填写名称。")
            lm=self._point(row['lm_pos'],width,height);tp=self._point(row['tp_pos'],width,height)
            icons.append(WorldPatrolLargeMapIcon(name,template,[lm.x,lm.y],[tp.x,tp.y]));ids.append(identifier)
        return icons,ids

    def _compose(self,sx,sy,dx,dy,size):
        old=self.state['transform'];self.state['transform']=[old[0]*sx,old[1]*sy,old[2]*sx+dx,old[3]*sy+dy]
        self.state['steps'].append((sx,sy,dx,dy,*size))

    def _shift(self,large,dx,dy):
        from zzz_od.application.world_patrol.world_patrol_area import WorldPatrolLargeMap,WorldPatrolLargeMapIcon
        import numpy as np
        height,width=large.road_mask.shape[:2];self._check_size(width+dx,height+dy)
        image=np.zeros((height+dy,width+dx),np.uint8);image[dy:dy+height,dx:dx+width]=large.road_mask
        icons=[WorldPatrolLargeMapIcon(i.icon_name,i.template_id,[i.lm_pos.x+dx,i.lm_pos.y+dy],[i.tp_pos.x+dx,i.tp_pos.y+dy]) for i in large.icon_list]
        return WorldPatrolLargeMap(large.area_full_id,image,icons)

    def _merge(self):
        from zzz_od.application.devtools.large_map_recorder import large_map_recorder_utils as utils
        from zzz_od.application.devtools.large_map_recorder.large_map_recorder_wrapper import LargeMapSnapshot
        from one_dragon.base.matcher.match_result import MatchResult
        from one_dragon.base.geometry.point import Point
        state=self.state;mini=state['mini'];large=state['large'];match=state['match']
        if mini is None:raise ValueError("请先取景或导入客户区截图。")
        if large.road_mask is None:
            result=utils.merge_large_map(None,mini,None)
            state['ids']=[uuid.uuid4().hex for _ in result.icon_list]
        else:
            if match is None:raise ValueError("请先定位，或点击地图设置候选中心。")
            to_use=utils.get_mini_map_in_circle(mini)
            h,w=to_use.road_mask.shape[:2];height,width=large.road_mask.shape[:2]
            left,top=max(0,-match.x),max(0,-match.y)
            right,bottom=max(0,match.x+w-width),max(0,match.y+h-height)
            self._check_size(width+left+right,height+top+bottom)
            # 先预留匹配矩形空间，避免上游切片越界；保持原图标的顺序与身份。
            if left or top:large=self._shift(large,left,top)
            if right or bottom:
                import cv2
                large.road_mask=cv2.copyMakeBorder(large.road_mask,0,bottom,0,right,cv2.BORDER_CONSTANT,value=0)
            adjusted=MatchResult(match.confidence,match.x+left,match.y+top,w,h)
            snapshot=LargeMapSnapshot(large,state['anchor']+Point(left,top))
            # 固定源码 merge_large_map 默认不复制道路；明确调用同一实际合并函数的 copy_road 分支。
            merged=utils._merge_at_position(snapshot,to_use,adjusted,copy_road=True)
            result=utils._expand_edges_if_needed(merged,(h,w))
            dx,dy=left+result.pos_after_merge.x-adjusted.center.x,top+result.pos_after_merge.y-adjusted.center.y
            self._compose(1,1,dx,dy,(result.road_mask.shape[1],result.road_mask.shape[0]))
            state['ids'].extend(uuid.uuid4().hex for _ in range(len(result.icon_list)-len(state['ids'])))
        self._check_size(result.road_mask.shape[1],result.road_mask.shape[0])
        result.area_full_id=state['key'];state['large']=result;state['anchor']=result.pos_after_merge;state['match']=None

    def _payload(self,value):
        import cv2,numpy as np
        from PIL import Image
        import io
        from one_dragon.base.geometry.point import Point
        from zzz_od.application.devtools.large_map_recorder.large_map_recorder_wrapper import MiniMapSnapshot
        if not isinstance(value,dict) or set(value) not in ({'mask','icons','warning'},{'mask','icons','warning','views'}) or not isinstance(value['mask'],str) or len(value['mask'])>700_000:raise ValueError("小地图快照格式无效。")
        raw=base64.b64decode(value['mask'],validate=True)
        with Image.open(io.BytesIO(raw)) as header:
            width,height=header.size
            if not 16<=width<=512 or width!=height:raise ValueError("小地图尺寸无效。")
        mask=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_GRAYSCALE)
        if mask is None:raise ValueError("小地图无法读取。")
        if np.any((mask!=0)&(mask!=255)):raise ValueError('道路掩码只能包含黑白像素。')
        if not isinstance(value['icons'],list) or len(value['icons'])>500:raise ValueError("小地图图标过多。")
        icons=[]
        for row in value['icons']:
            if not isinstance(row,dict) or set(row)!={'template_id','point'} or not isinstance(row['template_id'],str) or not re.fullmatch('map_icon_[0-9]{2}',row['template_id']):raise ValueError("小地图图标无效。")
            point=self._point(row['point'],width,height);icons.append((row['template_id'],Point(point.x,point.y)))
        if not isinstance(value['warning'],str) or len(value['warning'])>300:raise ValueError("快照提示无效。")
        views=value.get('views',[])
        if not isinstance(views,list) or len(views)>2:raise ValueError('取景预览数量无效。')
        for preview in views:
            if not isinstance(preview,str) or len(preview)>400_000:raise ValueError('取景预览过大。')
            with Image.open(io.BytesIO(base64.b64decode(preview,validate=True))) as header:
                if not 1<=header.width<=256 or not 1<=header.height<=256:raise ValueError('取景预览尺寸无效。')
                header.verify()
        return MiniMapSnapshot(mask,icons)

    def edit(self,data,action,value=None):
        self.service._assert_idle()
        with profile_lock(self.root):
            self.tx.assert_clear();state=self._current(data)
            if action not in ('import','append_frame','captured'):data_tree(value)
            before=copy.deepcopy(state);old_undo=self.undo
            from zzz_od.application.devtools.large_map_recorder import large_map_recorder_utils as utils
            from one_dragon.base.geometry.point import Point
            from one_dragon.base.matcher.match_result import MatchResult
            try:
                changed=action in ('merge','scale','icons')
                if action=='captured':state['mini']=self._payload(value);state['views']=list(value.get('views',[]));state['match']=None;state['warning']=value['warning']
                elif action=='import':
                    from .onedragon_route_recorder import decode_client_image
                    if not isinstance(value,list) or not 1<=len(value)<=2:raise ValueError("请选择同一位置的 1—2 张客户区截图。")
                    frames=[read_mini(self.ctx,decode_client_image(item),state['threshold']) for item in value]
                    snapshots=[item[0] for item in frames];state['views']=[item[1] for item in frames]
                    state['mini']=snapshots[0] if len(snapshots)==1 else utils.merge_mini_map(*snapshots)
                    state['match']=None;state['warning']=''
                elif action=='append_frame':
                    from .onedragon_route_recorder import decode_client_image
                    if state['mini'] is None:raise ValueError('请先导入第一张客户区截图。')
                    extra,view=read_mini(self.ctx,decode_client_image(value),state['threshold'])
                    if extra.road_mask.shape!=state['mini'].road_mask.shape:raise ValueError('两张小地图的尺寸不一致。')
                    state['mini']=utils.merge_mini_map(state['mini'],extra);state['views']=(state['views']+[view])[-2:];state['match']=None;state['warning']=''
                elif action=='locate':
                    if state['large'].road_mask is None or state['mini'] is None:raise ValueError("请先读取地图和取景。")
                    state['match']=utils.cal_pos(self.ctx,state['large'],state['mini'],state['anchor'],state['use_icons'])
                    state['warning']='未匹配到位置，请点击地图设置候选中心。' if state['match'] is None else ''
                elif action in ('anchor','offset'):
                    image=state['large'].road_mask
                    if image is None:raise ValueError("请先建立地图。")
                    height,width=image.shape[:2]
                    if action=='offset':
                        if not isinstance(value,list) or len(value)!=2 or any(type(x)is not int or abs(x)>10000 for x in value):raise ValueError("位置偏移须为 ±10000 内的两个整数。")
                        point=state['match'].center if state['match'] is not None else state['anchor'];value=[point.x+value[0],point.y+value[1]]
                    state['anchor']=self._point(value,width,height)
                    if state['mini'] is not None:
                        h,w=state['mini'].road_mask.shape[:2];state['match']=MatchResult(1,state['anchor'].x-w//2,state['anchor'].y-h//2,w,h)
                elif action=='merge':self._merge()
                elif action=='scale':
                    if type(value) not in (int,float) or not 25<=value<=400:raise ValueError("缩放比例须为 25—400%。")
                    import cv2
                    from zzz_od.application.world_patrol.world_patrol_area import WorldPatrolLargeMap,WorldPatrolLargeMapIcon
                    large=state['large']
                    if large.road_mask is None:raise ValueError("请先建立地图。")
                    height,width=large.road_mask.shape[:2];size=(round(width*value/100),round(height*value/100));self._check_size(*size)
                    sx,sy=size[0]/width,size[1]/height
                    def scaled(point):return [min(size[0]-1,round(point.x*sx)),min(size[1]-1,round(point.y*sy))]
                    icons=[WorldPatrolLargeMapIcon(i.icon_name,i.template_id,scaled(i.lm_pos),scaled(i.tp_pos)) for i in large.icon_list]
                    state['large']=WorldPatrolLargeMap(state['key'],cv2.resize(large.road_mask,size,interpolation=cv2.INTER_NEAREST),icons)
                    state['anchor']=Point(*scaled(state['anchor']));state['match']=None;self._compose(sx,sy,0,0,size)
                elif action=='icons':state['large'].icon_list,state['ids']=self._icons(value)
                elif action=='threshold':
                    if type(value) not in (float,int) or not .1<=value<=1:raise ValueError("图标匹配阈值须为 0.1—1。")
                    state['threshold']=float(value)
                elif action=='settings':
                    if not isinstance(value,dict) or set(value)!={'threshold','use_icons'} or type(value['threshold']) not in (float,int) or not .1<=value['threshold']<=1 or type(value['use_icons'])is not bool:raise ValueError('地图定位参数无效。')
                    state['threshold']=float(value['threshold']);state['use_icons']=value['use_icons'];state['match']=None
                elif action=='use_icons':
                    if type(value)is not bool:raise ValueError("图标定位须为开关。")
                    state['use_icons']=value
                elif action=='undo':
                    if self.undo is None:raise ValueError("没有可回退的地图修改。")
                    self.state=self.undo;self.undo=None;self.state['version']=state['version'];state=self.state
                else:raise ValueError("地图操作无效。")
                if changed:state['dirty']=True;self.undo=before
                state['version']+=1
                self.preferences=dict(threshold=state['threshold'],use_icons=state['use_icons'])
                return self._document()
            except Exception:self.state=before;self.undo=old_undo;raise

    def _routes(self,key):
        from one_dragon.utils.yaml_utils import safe_load
        from zzz_od.application.world_patrol.world_patrol_route import WorldPatrolRoute
        result=[];area=self._area(key)
        for path in self._paths(key)[2]:
            try:
                raw=self.tx.read(path)
                if len(raw)>768*1024:raise ValueError('路线过大')
                data=safe_load(raw);data_tree(data)
                if not isinstance(data,dict) or type(data.get('idx'))is not int or data.get('tp_area_id')!=key:raise ValueError('路线字段无效')
                route=WorldPatrolRoute.from_dict(data,area)
                if self.library._path('world_patrol_route',route.full_id)!=path:raise ValueError('路线编号与文件不一致')
                for operation in route.op_list:
                    if operation.op_type!='move' or not isinstance(operation.data,list) or len(operation.data)!=2:raise ValueError('路线操作无效')
                    for number in operation.data:coordinate(number,2**31)
                result.append((path,data,raw))
            except Exception as exc:raise ValueError('无法迁移路线 '+str(path.relative_to(self.root))+'：'+str(exc)) from None
        return result

    def _metadata(self,key,before,after,count):
        return dict(area_id=key,created_at=datetime.now().astimezone().isoformat(),before_revision=before,after_revision=after,route_count=count)

    def save(self,key,data,revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            self.tx.assert_clear();state=self._current(data)
            if key!=state['key'] or revision!=state['revision'] or self.revision(key)!=revision:raise ValueError("地图或关联路线已变化，请重新读取。")
            large=state['large']
            if large.road_mask is None:raise ValueError("请先建立地图。")
            icons,ids=self._icons([dict(i.to_dict(),id=identifier) for i,identifier in zip(large.icon_list,state['ids'])],strict=True)
            import cv2,yaml
            ok,png=cv2.imencode('.png',large.road_mask)
            if not ok:raise ValueError("地图编码失败。")
            road,icon_path,_=self._paths(key)
            writes={road:png.tobytes(),icon_path:yaml.safe_dump([i.to_dict() for i in icons],allow_unicode=True,sort_keys=False).encode('utf-8')}
            routes=self._routes(key)
            if routes and state['base_missing']:raise ValueError("原地图缺失，无法确定已有路线坐标，请先恢复原地图。")
            new_icons=dict(zip(ids,icons));changed_routes=0
            for path,original,raw in routes:
                identifier=next((identifier for identifier,row in self.base_icons.items() if row['template_id']=='map_icon_01' and row['icon_name']==original['tp_name']),None)
                target=new_icons.get(identifier)
                if target is None or target.template_id!='map_icon_01' or not target.icon_name:raise ValueError('不能移除仍被路线引用的传送点：'+str(original['tp_name']))
                updated=copy.deepcopy(original);updated['tp_name']=target.icon_name
                for operation in updated['op_list']:
                    previous=operation['data'];x=coordinate(previous[0],state['base_size'][0]);y=coordinate(previous[1],state['base_size'][1])
                    for sx,sy,dx,dy,width,height in state['steps']:
                        x=min(width-1,max(0,round(sx*x+dx)));y=min(height-1,max(0,round(sy*y+dy)))
                    operation['data']=[str(number) if isinstance(old,str) else number for number,old in zip((x,y),previous)]
                if updated!=original:writes[path]=yaml.safe_dump(updated,allow_unicode=True,sort_keys=False).encode('utf-8');changed_routes+=1
            scope=self._scope(key);after=dict(scope,**{path.relative_to(self.root).as_posix():raw for path,raw in writes.items()})
            archive=self.tx.publish(writes,self._metadata(key,revision,scope_revision(after),changed_routes),lambda:self.revision(key)==revision)
            self.library._refresh();result=self._read(key);result['notice']=f'已保存地图及 {changed_routes} 条关联路线';result['archive_id']=archive
            return result

    def remove(self,key,revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            self.tx.assert_clear()
            if self.revision(key)!=revision:raise ValueError('地图或路线已变化，请重新读取。')
            road,icons,routes=self._paths(key)
            if routes:raise ValueError('地图仍被路线引用，请先处理关联路线。')
            if not road.exists() and not icons.exists():raise ValueError('当前地图尚未保存。')
            writes={road:None,icons:None};after={path.relative_to(self.root).as_posix():None for path in writes}
            identifier=self.tx.publish(writes,self._metadata(key,revision,scope_revision(after),0),lambda:self.revision(key)==revision)
            self.library._refresh();self.state=None;self.undo=None
            return dict(archive_id=identifier,restored_template=False)

    def archives(self,key):
        self.service._assert_idle();self._area(key)
        with profile_lock(self.root):
            rows=[];issues=[];current=self.revision(key)
            for path in (self.root/'archives/maps').glob('*/manifest.json'):
                try:
                    manifest,_,_=self.tx.load(path.parent.name);meta=manifest['metadata']
                    if meta.get('area_id')==key:rows.append(dict(id=path.parent.name,key=self._area(key).full_name,created_at=meta['created_at'],route_count=meta['route_count'],restorable=meta['after_revision']==current))
                except Exception as exc:issues.append(dict(file=str(path.relative_to(self.root)),error=str(exc)))
            return dict(items=sorted(rows,key=lambda row:row['created_at'],reverse=True),issues=issues)

    def restore(self,key,identifier,revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            self.tx.assert_clear();manifest,files,_=self.tx.load(identifier);meta=manifest['metadata']
            if meta.get('area_id')!=key or revision!=self.revision(key) or meta.get('after_revision')!=revision:raise ValueError('归档后地图或路线已有修改，不能覆盖。')
            if any(fingerprint(self.tx.read(path))!=fingerprint(new) for path,_,new in files):raise ValueError('归档后的文件已变化。')
            writes={path:old for path,old,_ in files}
            self.tx.publish(writes,self._metadata(key,revision,meta['before_revision'],meta['route_count']),lambda:self.revision(key)==revision)
            self.library._refresh();return self._read(key)

    def recover(self):
        self.service._assert_idle()
        with profile_lock(self.root):
            meta=self.tx.recover();self.library._refresh();self.state=None;self.undo=None
            return dict(recovered=meta is not None,area_id=None if meta is None else meta.get('area_id'))
