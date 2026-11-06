"""地图双视角取景；观察过期后使用新帧，不重复已尝试的转镜头。"""
from .onedragon_control import ObservationExpired
from .onedragon_route_recorder import RouteCaptureJob
from .onedragon_maps import read_mini, snapshot_payload
from .run_lock import profile_lock


class MapCaptureJob(RouteCaptureJob):
    def start_map(self, data, game_path, rotate):
        self.service._assert_idle()
        if type(rotate)is not bool:raise ValueError("转镜头选项须为开关。")
        store=self.service._map_store()
        with profile_lock(self.service.data):
            store.tx.assert_clear();state=store._current(data)
            self.threshold=state['threshold']
            self.draft_id=state['id'];self.draft_version=state['version']
        self.rotate=rotate;self.turn_attempted=False
        return self._start(None,None,game_path)

    def _calculate(self, large_map, anchor, screen):
        first,view=read_mini(self.service.ctx,screen,self.threshold)
        if not self.rotate:return snapshot_payload(first,views=[view])
        if self.turn_attempted:
            return snapshot_payload(first,"转镜头被中断，已使用恢复后的单帧；请核对后再合并。",views=[view])
        self.turn_attempted=True
        self.controller.turn_by_angle_diff(180)
        if not self.control.hold(2):raise ObservationExpired("等待恢复后重新取景。")
        _,second=self.controller.screenshot()
        second,second_view=read_mini(self.service.ctx,second,self.threshold)
        from zzz_od.application.devtools.large_map_recorder.large_map_recorder_utils import merge_mini_map
        return snapshot_payload(merge_mini_map(first,second),views=[view,second_view])

    def status(self,job_id):
        return dict(super().status(job_id),kind='map',draft_id=self.draft_id,draft_version=self.draft_version)

    def result_for(self, data, value):
        with self.lock:
            # Older callers return the preview itself. Accept it only when it is
            # identical to this job's server-side result and passes the same checks.
            matches = value == self.job_id if isinstance(value, str) else isinstance(value, dict) and value == self.result
            if self.busy or self.state != 'success' or not matches or self.result is None:
                raise ValueError('取景结果已失效，请重新取景。')
            if (data['draft_id'], data['version']) != (self.draft_id, self.draft_version):
                raise ValueError('取景后地图草稿已变化，请重新取景。')
            self.origin.require_current(self.service)
            return self.result, self.origin
