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
