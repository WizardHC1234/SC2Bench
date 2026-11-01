"""Native command locks also cover auto-defense and worker controllers."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.buff_id import BuffId as B
from sharpy.knowledges import KnowledgeBot
from sc2bench_env.backends.sharpy.bot import BenchBot


@pytest.mark.parametrize('kind',[U.SCV,U.PROBE,U.DRONE,U.MARINE,U.STALKER,U.HYDRALISK])
def test_lifted_units_wait_until_released_without_sending_or_deducting(monkeypatch,kind):
    parent=MagicMock(return_value=True);monkeypatch.setattr(KnowledgeBot,'do',parent)
    bot=object.__new__(BenchBot)
    bot.actions=[]
    unit=NS(type_id=kind,buffs=[B.GRAVITONBEAM],orders=[])
    command=NS(unit=unit)
    assert bot.do(command,subtract_cost=True) is False
    parent.assert_not_called()
    unit.buffs=[]
    assert bot.do(command,subtract_cost=True) is True
    assert parent.call_args.kwargs['subtract_cost'] is True


@pytest.mark.parametrize('kind,ability',[(U.VIKINGASSAULT,A.MORPH_VIKINGFIGHTERMODE),
    (U.THOR,A.MORPH_THORHIGHIMPACTMODE),(U.THORAP,A.MORPH_THOREXPLOSIVEMODE)])
def test_auto_defense_cannot_repeat_a_morph_in_progress(monkeypatch,kind,ability):
    parent=MagicMock(return_value=True);monkeypatch.setattr(KnowledgeBot,'do',parent)
    bot=object.__new__(BenchBot)
    bot.actions=[]
    unit=NS(type_id=kind,buffs=[],orders=[NS(ability=NS(id=ability))])
    assert bot.do(NS(unit=unit)) is False
    parent.assert_not_called()
    unit.orders=[]
    assert bot.do(NS(unit=unit)) is True


@pytest.mark.parametrize('kind',[U.MARINE,U.COLOSSUS,U.HYDRALISK])
@pytest.mark.parametrize('ability',[A.ATTACK,A.ATTACK_ATTACK])
def test_temporarily_unavailable_native_attack_waits_and_resumes(monkeypatch,kind,ability):
    parent=MagicMock(return_value=True);monkeypatch.setattr(KnowledgeBot,'do',parent)
    bot=object.__new__(BenchBot);bot.actions=[]
    available={1:[A.MOVE_MOVE,A.STOP_STOP]}
    bot.knowledge=NS(cooldown_manager=NS(available_dict=available))
    unit=NS(type_id=kind,tag=1,buffs=[],orders=[])
    command=NS(unit=unit,ability=ability)
    assert bot.do(command,subtract_cost=True) is False
    parent.assert_not_called()
    available[1]=[A.ATTACK_ATTACK,A.MOVE_MOVE]
    assert bot.do(command,subtract_cost=True) is True
    parent.assert_called_once()


def test_missing_native_ability_query_is_unknown_rather_than_a_disabled_attack(monkeypatch):
    parent=MagicMock(return_value=True);monkeypatch.setattr(KnowledgeBot,'do',parent)
    bot=object.__new__(BenchBot);bot.actions=[]
    bot.knowledge=NS(cooldown_manager=NS(available_dict={}))
    unit=NS(type_id=U.COLOSSUS,tag=1,buffs=[],orders=[])
    assert bot.do(NS(unit=unit,ability=A.ATTACK)) is True
    parent.assert_called_once()


@pytest.mark.parametrize('role,ability,queued,interrupted',[(6,A.ATTACK,True,True),(0,A.ATTACK,True,False),
    (6,A.EFFECT_INJECTLARVA,True,False),(6,A.ATTACK,False,False)])
def test_actual_defense_command_marks_a_pending_spell_interruption(monkeypatch,role,ability,queued,interrupted):
    def send(ai,action,**kwargs):
        if queued:ai.actions.append(action)
        return True
    monkeypatch.setattr(KnowledgeBot,'do',send)
    bot=object.__new__(BenchBot)
    bot.actions=[]
    unit=NS(type_id=U.QUEEN,tag=1,buffs=[],orders=[])
    cast={'caster':1,'ability':A.EFFECT_INJECTLARVA}
    bot.roles=NS(unit_role=lambda _:role)
    bot._macro_tasks=[{'_act':NS(_pending_cast=cast)}]
    assert bot.do(NS(unit=unit,ability=ability)) is True
    assert bool(cast.get('interrupted_by_combat'))==interrupted
