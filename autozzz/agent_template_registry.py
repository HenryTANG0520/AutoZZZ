"""Map template IDs to fixed engine agents; never invent unknown agent skills."""


def registrations(agents=None):
    if agents is None:
        from zzz_od.game_data.agent import AgentEnum
        agents=[item.value for item in AgentEnum]
    result={}
    for agent in agents:
        if agent.agent_id=='unknown':continue
        for template_id in dict.fromkeys([agent.agent_id,*agent.template_id_list]):
            row=dict(template_id=template_id,agent_id=agent.agent_id,name=agent.agent_name,
                     registered=True,selected=agent.template_id==template_id)
            if template_id in result and result[template_id]['agent_id']!=agent.agent_id:
                raise ValueError('源引擎模板ID重复：'+template_id)
            result[template_id]=row
    return result


def recognition(template_id,agents=None):
    return registrations(agents).get(template_id,dict(template_id=template_id,agent_id=None,name='',registered=False,selected=False))


def require_registered(template_id,agents=None):
    result=recognition(template_id,agents)
    if not result['registered']:
        raise ValueError('引擎未注册模板ID '+template_id+'；请选择已有角色或外观ID。保存图片不能新增角色状态或战斗技能。')
    return result
