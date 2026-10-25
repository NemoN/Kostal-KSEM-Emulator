"""Read-only KSEM register context shared by real RTU and simulated-client tests."""

import time

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext

from .registers import split_u64
from .snapshot import ENERGY_REGISTERS


class KsemContext(ModbusDeviceContext):
    def __init__(self, store):
        super().__init__(hr=ModbusSequentialDataBlock(0, [0]))
        self.snapshots = store

    def getValues(self, func_code, address, count=1):
        if func_code not in (3, 4):
            return ExcCodes.ILLEGAL_FUNCTION
        if not 1 <= count <= 125 or address < 0:
            return ExcCodes.ILLEGAL_ADDRESS
        registers = self.snapshots.read()
        if registers is None:
            return ExcCodes.DEVICE_FAILURE
        end = address + count
        if 0 <= address and end <= 148:
            # Upstream buffer uses N+1; this context receives the zero-based wire address.
            return list(registers[address + 1 : end + 1])
        if 8244 <= address and end <= 8250:
            pnp = [1000, *split_u64(int(time.time() * 1000)), 7]
            return pnp[address - 8244 : end - 8244]
        if self.snapshots.energy_registers:
            for start in ENERGY_REGISTERS.values():
                if start <= address and end <= start + 4:
                    return list(registers[address + 1 : end + 1])
        return ExcCodes.ILLEGAL_ADDRESS

    def setValues(self, func_code, address, values):
        return ExcCodes.ILLEGAL_FUNCTION


def server_context(store):
    return ModbusServerContext(devices={1: KsemContext(store)}, single=False)
