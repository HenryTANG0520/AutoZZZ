"""Choose the daily drink from the account's saved charge plan."""
from __future__ import annotations


def choose_coffee(plans, loop=False, *, reset_due=False):
    for plan in plans:
        get = plan.get if isinstance(plan, dict) else lambda key, default=None: getattr(plan, key, default)
        if (get('category_name') == '实战模拟室'
                and get('plan_times', 1) > 0
                and (loop or get('plan_times', 1) > (0 if reset_due else get('run_times', 0)))
                and not get('skipped', False)):
            return '鲜萃红茶'
    return '汀曼特调'


def configured_coffee(config, account):
    reset_due = False
    if getattr(config, 'daily_reset_plan_times', False):
        from one_dragon.utils import os_utils

        # Use the same refresh day as ChargePlanRunRecord, without resetting progress here.
        reset_due = config.last_daily_reset_dt != os_utils.get_dt(account.game_refresh_hour_offset)
    return choose_coffee(config.plan_list, config.loop, reset_due=reset_due)


def saved_coffee(root, server):
    from .onedragon_runtime import OneDragonClient, RuntimePaths, RequestPending
    client = OneDragonClient(RuntimePaths.discover(root, server))
    try:
        client.start()
        request = client.submit('describe_configuration', app_id='charge_plan')
        while True:
            try:
                description = client.result(request, timeout=5)
                break
            except RequestPending:
                continue
        name = description.get('daily_coffee')
        if name is not None:
            return name
        fields = {f['name']: f['value'] for f in description['fields']}
        return choose_coffee(fields['plan_list'], fields.get('loop', False))
    finally:
        while True:
            try:
                client.close(timeout=5)
                break
            except RequestPending:
                continue


class CoffeeSelectionControl:
    def __init__(self, ctx):
        self.ctx = ctx
        self.original = None

    def install(self):
        from zzz_od.application.coffee.coffee_app import CoffeeApp
        from zzz_od.game_data.compendium import Coffee
        from one_dragon.base.operation.application.application_const import DEFAULT_GROUP_ID
        self.original = CoffeeApp._get_coffee_to_choose
        original, ctx = self.original, self.ctx

        def candidates(app):
            if app.ctx is not ctx:
                return original(app)
            config = ctx.run_context.get_config('charge_plan', ctx.current_instance_idx, DEFAULT_GROUP_ID)
            name = configured_coffee(config, ctx.game_account_config)
            if name not in ctx.compendium_service.name_2_coffee:
                ctx.compendium_service.name_2_coffee[name] = Coffee(name, None, None, None, None)
            extras = [coffee.coffee_name for coffee in ctx.compendium_service.get_extra_coffee_list()
                      if coffee.coffee_name not in app.had_coffee_list]
            return extras + ([] if name in app.had_coffee_list else [name])

        CoffeeApp._get_coffee_to_choose = candidates

    def close(self):
        if self.original is not None:
            from zzz_od.application.coffee.coffee_app import CoffeeApp
            CoffeeApp._get_coffee_to_choose = self.original
            self.original = None
