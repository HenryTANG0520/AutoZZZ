"""使用真实上游解释器和源码验证通信、配置及条件策略，不访问游戏。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import OneDragonClient, RuntimePaths, RemoteError


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    default = RuntimePaths.discover(root)
    with tempfile.TemporaryDirectory(prefix="autozzz-onedragon-") as directory:
        paths = RuntimePaths(default.source, default.python, Path(directory))
        logs = []
        client = OneDragonClient(paths, log=logs.append)
        try:
            hello = client.start()
            catalog = client.call("catalog", timeout=60)
            assert not catalog["failures"], catalog["failures"]
            assert not catalog["ready_for_game"]
            apps = {app["id"]: app for app in catalog["applications"]}
            assert len(apps) == 30, sorted(apps)
            assert apps["auto_battle"]["internal"]
            assert apps["charge_plan"]["configurable"]
            configured = []
            for app in apps.values():
                if app["configurable"]:
                    snapshot = client.call("configuration", app_id=app["id"])
                    assert not snapshot["failures"], snapshot
                    configured.append(app["id"])
            coffee = client.call("configuration", app_id="coffee")
            assert coffee["fields"] and not coffee["failures"], coffee
            policies = client.call("policies")
            assert any(policy["name"] == "全配队通用" for policy in policies)
            text = client.call("policy_read", name="全配队通用")
            assert text["content"].strip()
            validation = client.call("policy_validate", name="全配队通用", timeout=60)
            assert len(validation["scenes"]) > 1 and not validation["game_input_sent"]
            custom = 'author: AutoZZZ\nscenes:\n  - handlers:\n      - states: ""\n        operations:\n          - op_name: 等待秒数\n            seconds: 0.01\n'
            saved = client.call("policy_save", name="我的策略", content=custom)
            assert len(saved["scenes"]) == 1
            assert client.call("policy_read", name="我的策略")["content"] == custom
            checked = client.call("policy_check", content=custom)
            assert len(checked["scenes"]) == 1
            for method, params in (
                ("policy_save", dict(name="我的策略", content="scenes: [invalid]")),
                ("policy_save", dict(name="../outside", content=custom)),
                ("policy_save", dict(name="全配队通用", content=custom)),
                ("policy_validate", dict(name="不存在的策略")),
                ("set_battle_options", dict(values={"auto_battle_config": "不存在的策略"})),
                ("run", dict(app_id="charge_plan")),
            ):
                try:
                    client.call(method, **params)
                    raise AssertionError(f"必须拒绝请求：{method} {params}")
                except RemoteError:
                    pass
            assert client.call("policy_read", name="我的策略")["content"] == custom
            assert not list((paths.data / "config" / "auto_battle").glob("autozzz-check-*"))
            options = client.call("set_battle_options", values={"auto_battle_config": "我的策略"})
            assert options["fields"]["auto_battle_config"] == "我的策略"
            client.call("set_battle_options", values={"use_merged_file": False})
            (paths.data / "config" / "auto_battle" / "我的策略.merged.yml").write_text("scenes: [invalid]", encoding="utf-8")
            # 已选择原始版本时，不能意外校验或使用同名合并版本。
            options = client.call("set_battle_options", values={"auto_battle_config": "我的策略"})
            assert options["fields"]["use_merged_file"] is False
            assert not client.call("catalog")["ready_for_game"]
            print(json.dumps(dict(python=hello["python"], revision=hello["revision"],
                                  applications=len(apps), policies=len(policies),
                                  configuration_snapshots=len(configured), rejected_requests=6,
                                  validated_scenes=len(validation["scenes"]), custom_policy_saved=True,
                                  input_enabled=hello["input_enabled"], game_started=False), ensure_ascii=False))
        except Exception:
            print("\n".join(logs[-35:]), file=sys.stderr)
            raise
        finally:
            client.close()


if __name__ == "__main__":
    main()
