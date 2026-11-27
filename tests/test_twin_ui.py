import pytest

from rail_vehicle.coupled_vehicle import CoupledCase
from rail_vehicle.pages.twin import prompt_defaults, proposal_prompt
from rail_vehicle.twin_workflow import demo_plan


def test_copilot_range_parser_requires_exact_physical_units():
    original=demo_plan(CoupledCase())
    text="校准当前模型，二系横向刚度 85–260 kN/m，侧滚不超过 4 mrad，优化并复算。"
    assert proposal_prompt(text)
    draft=prompt_defaults(text,original)
    assert draft.bounds["secondary_y_k_n_m"]==(85_000,260_000)
    assert draft.max_roll_mrad==4
    with pytest.raises(ValueError,match="单位"):
        prompt_defaults(text.replace("kN/m","kN/mm"),original)
