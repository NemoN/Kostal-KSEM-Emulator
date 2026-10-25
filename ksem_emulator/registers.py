"""KSEM encoding derived from JsBergbau/Kostal-KSEM-Emulator, GPL-3.0.

Modified in this fork: validated inputs, measured frequency and energy counters.
"""

import math

REGISTER_COUNT = 9000
GRID_FREQUENCY_HZ = 50.0


def split_u32(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def split_u64(value: int) -> list[int]:
    value &= 0xFFFFFFFFFFFFFFFF
    return [
        (value >> 48) & 0xFFFF,
        (value >> 32) & 0xFFFF,
        (value >> 16) & 0xFFFF,
        value & 0xFFFF,
    ]


def apparent_power(volt: float, amp: float) -> float:
    return volt * amp


def reactive_power(power_w: float, voltage_v: float, current_a: float) -> float:
    s = apparent_power(voltage_v, current_a)
    return math.sqrt(max(s * s - power_w * power_w, 0.0))


def safe_pf(power_w: float, apparent_va: float) -> float:
    if apparent_va <= 0:
        return 0.0
    return abs(power_w) / apparent_va


def raw_power(value_w_or_var_or_va: float) -> int:
    return int(round(value_w_or_var_or_va * 10.0))


def raw_current(value_a: float) -> int:
    return int(round(value_a * 1000.0))


def raw_voltage(value_v: float) -> int:
    return int(round(value_v * 1000.0))


def raw_pf(value_pf: float) -> int:
    return int(round(value_pf * 1000.0))


def raw_frequency(value_hz: float) -> int:
    return int(round(value_hz * 1000.0))


def split_by_active_direction(active_power_w: float, magnitude: float) -> tuple[float, float]:
    """
    Ordnet einen Betrag derselben Richtung zu wie die Wirkleistung:

    - active_power_w >= 0  -> Bezug / '+'-Register
    - active_power_w < 0   -> Einspeisung / '-'-Register

    Das benutzen wir für Blind- und Scheinleistung, weil der Shelly dort
    keinen eigenen Richtungswert liefert. So bleiben Q und S konsistent
    zur Richtung von P.
    """
    if active_power_w < 0:
        return 0.0, magnitude
    return magnitude, 0.0


def build_runtime_registers_from_snapshot(data: dict) -> list[int]:
    values = [0] * REGISTER_COUNT

    def set_u32_doc(doc_reg: int, value: int) -> None:
        hi, lo = split_u32(value)
        values[doc_reg + 1] = hi
        values[doc_reg + 2] = lo

    emeters = data.get("emeters", [])
    if len(emeters) < 3:
        return values

    e1, e2, e3 = emeters[0], emeters[1], emeters[2]

    p1 = float(e1.get("power", 0.0))
    p2 = float(e2.get("power", 0.0))
    p3 = float(e3.get("power", 0.0))

    u1 = float(e1.get("voltage", 0.0))
    u2 = float(e2.get("voltage", 0.0))
    u3 = float(e3.get("voltage", 0.0))

    i1 = float(e1.get("current", 0.0))
    i2 = float(e2.get("current", 0.0))
    i3 = float(e3.get("current", 0.0))

    s1 = apparent_power(u1, i1)
    s2 = apparent_power(u2, i2)
    s3 = apparent_power(u3, i3)

    q1 = reactive_power(abs(p1), u1, i1)
    q2 = reactive_power(abs(p2), u2, i2)
    q3 = reactive_power(abs(p3), u3, i3)

    pf1 = float(e1.get("pf", 0.0)) or safe_pf(p1, s1)
    pf2 = float(e2.get("pf", 0.0)) or safe_pf(p2, s2)
    pf3 = float(e3.get("pf", 0.0)) or safe_pf(p3, s3)

    p_total = p1 + p2 + p3
    s_total = s1 + s2 + s3
    q_total = q1 + q2 + q3
    pf_total = safe_pf(p_total, s_total)

    # Wirkleistung hat echte Richtung:
    # Bezug  -> '+'-Register
    # Einspeisung -> '-'-Register
    p1_plus = max(p1, 0.0)
    p1_minus = max(-p1, 0.0)
    p2_plus = max(p2, 0.0)
    p2_minus = max(-p2, 0.0)
    p3_plus = max(p3, 0.0)
    p3_minus = max(-p3, 0.0)

    p_total_plus = max(p_total, 0.0)
    p_total_minus = max(-p_total, 0.0)

    # Für Blind- und Scheinleistung liefert der Shelly keinen eigenen
    # Richtungswert. Deshalb legen wir Q und S in dieselbe Richtung
    # wie die jeweilige Wirkleistung.
    q_total_plus, q_total_minus = split_by_active_direction(p_total, q_total)
    s_total_plus, s_total_minus = split_by_active_direction(p_total, s_total)

    q1_plus, q1_minus = split_by_active_direction(p1, q1)
    q2_plus, q2_minus = split_by_active_direction(p2, q2)
    q3_plus, q3_minus = split_by_active_direction(p3, q3)

    s1_plus, s1_minus = split_by_active_direction(p1, s1)
    s2_plus, s2_minus = split_by_active_direction(p2, s2)
    s3_plus, s3_minus = split_by_active_direction(p3, s3)

    freq = GRID_FREQUENCY_HZ

    # ----------------------------
    # Gesamt
    # ----------------------------
    set_u32_doc(0, raw_power(p_total_plus))  # Active power+
    set_u32_doc(2, raw_power(p_total_minus))  # Active power-
    set_u32_doc(4, raw_power(q_total_plus))  # Reactive power+
    set_u32_doc(6, raw_power(q_total_minus))  # Reactive power-
    set_u32_doc(16, raw_power(s_total_plus))  # Apparent power+
    set_u32_doc(18, raw_power(s_total_minus))  # Apparent power-
    set_u32_doc(24, raw_pf(pf_total))  # Power factor
    set_u32_doc(26, raw_frequency(freq))  # Supply frequency

    # ----------------------------
    # L1
    # ----------------------------
    set_u32_doc(40, raw_power(p1_plus))
    set_u32_doc(42, raw_power(p1_minus))
    set_u32_doc(44, raw_power(q1_plus))
    set_u32_doc(46, raw_power(q1_minus))
    set_u32_doc(56, raw_power(s1_plus))
    set_u32_doc(58, raw_power(s1_minus))
    set_u32_doc(60, raw_current(i1))
    set_u32_doc(62, raw_voltage(u1))
    set_u32_doc(64, raw_pf(pf1))

    # ----------------------------
    # L2
    # ----------------------------
    set_u32_doc(80, raw_power(p2_plus))
    set_u32_doc(82, raw_power(p2_minus))
    set_u32_doc(84, raw_power(q2_plus))
    set_u32_doc(86, raw_power(q2_minus))
    set_u32_doc(96, raw_power(s2_plus))
    set_u32_doc(98, raw_power(s2_minus))
    set_u32_doc(100, raw_current(i2))
    set_u32_doc(102, raw_voltage(u2))
    set_u32_doc(104, raw_pf(pf2))

    # ----------------------------
    # L3
    # ----------------------------
    set_u32_doc(120, raw_power(p3_plus))
    set_u32_doc(122, raw_power(p3_minus))
    set_u32_doc(124, raw_power(q3_plus))
    set_u32_doc(126, raw_power(q3_minus))
    set_u32_doc(136, raw_power(s3_plus))
    set_u32_doc(138, raw_power(s3_minus))
    set_u32_doc(140, raw_current(i3))
    set_u32_doc(142, raw_voltage(u3))
    set_u32_doc(144, raw_pf(pf3))

    return values
