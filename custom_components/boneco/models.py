from dataclasses import dataclass

from pyboneco import BonecoDeviceInfo, BonecoDeviceState


@dataclass
class BonecoCombinedState:
    name: str
    info: BonecoDeviceInfo
    state: BonecoDeviceState
