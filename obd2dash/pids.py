"""OBD-II mode 01 PID catalogue (SAE J1979) and decoders for DTCs, monitors and negative responses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .i18n import Msg


def _L(pt: str, en: str) -> Msg:
    return Msg(pt=pt, en=en)


@dataclass(frozen=True, eq=False)
class PID:
    pid: int
    key: str
    label: Msg
    unit: str
    nbytes: int
    fn: Callable[[bytes], object]
    interval: float = 5.0     # desired seconds between live reads
    dec: int = 0              # decimals to display
    live: bool = True         # polled live?


def _ab(d: bytes) -> int:
    return d[0] * 256 + d[1]


def _pct(d: bytes) -> float:
    return d[0] * 100.0 / 255


def _temp(d: bytes) -> int:
    return d[0] - 40


def _trim(d: bytes) -> float:
    return d[0] * 100.0 / 128 - 100


def _lambda(d: bytes) -> float:
    return _ab(d) * 2.0 / 65536


FUEL_SYSTEM: Dict[int, Msg] = {
    1: _L("Malha aberta (motor frio)", "Open loop (engine cold)"),
    2: _L("Malha fechada (usando sensor de O2)", "Closed loop (using O2 sensor)"),
    4: _L("Malha aberta (condição de condução)", "Open loop (driving condition)"),
    8: _L("Malha aberta (falha detectada)", "Open loop (fault detected)"),
    16: _L("Malha fechada (falha no sensor de O2)", "Closed loop (O2 sensor fault)"),
}

_FUEL_TYPES = [
    ("Não disponível", "Not available"), ("Gasolina", "Gasoline"), ("Metanol", "Methanol"), ("Etanol", "Ethanol"),
    ("Diesel", "Diesel"), ("GLP", "LPG"), ("GNV", "CNG"), ("Propano", "Propane"), ("Elétrico", "Electric"),
    ("Bicombustível (gasolina)", "Bi-fuel (gasoline)"), ("Bicombustível (metanol)", "Bi-fuel (methanol)"),
    ("Bicombustível (etanol)", "Bi-fuel (ethanol)"), ("Bicombustível (GLP)", "Bi-fuel (LPG)"),
    ("Bicombustível (GNV)", "Bi-fuel (CNG)"), ("Bicombustível (propano)", "Bi-fuel (propane)"),
    ("Bicombustível (elétrico)", "Bi-fuel (electric)"), ("Bicombustível (elétrico/combustão)", "Bi-fuel (electric/combustion)"),
    ("Híbrido (gasolina)", "Hybrid (gasoline)"), ("Híbrido (etanol)", "Hybrid (ethanol)"),
    ("Híbrido (diesel)", "Hybrid (diesel)"), ("Híbrido (elétrico)", "Hybrid (electric)"),
    ("Híbrido (misto)", "Hybrid (mixed)"), ("Híbrido (regenerativo)", "Hybrid (regenerative)"),
]
FUEL_TYPES: Dict[int, Msg] = {i: _L(pt, en) for i, (pt, en) in enumerate(_FUEL_TYPES)}

OBD_STANDARDS: Dict[int, Msg] = {
    1: _L("OBD-II (CARB)", "OBD-II (CARB)"), 2: _L("OBD (EPA)", "OBD (EPA)"), 3: _L("OBD e OBD-II", "OBD and OBD-II"),
    4: _L("OBD-I", "OBD-I"), 5: _L("Não compatível com OBD", "Not OBD compliant"), 6: _L("EOBD (Europa)", "EOBD (Europe)"),
    7: _L("EOBD e OBD-II", "EOBD and OBD-II"), 8: _L("EOBD e OBD", "EOBD and OBD"), 9: _L("EOBD, OBD e OBD-II", "EOBD, OBD and OBD-II"),
    10: _L("JOBD (Japão)", "JOBD (Japan)"), 11: _L("JOBD e OBD-II", "JOBD and OBD-II"), 12: _L("JOBD e EOBD", "JOBD and EOBD"),
    13: _L("JOBD, EOBD e OBD-II", "JOBD, EOBD and OBD-II"),
    28: _L("OBD Brasil fase 1 (OBDBr-1)", "Brazil OBD phase 1 (OBDBr-1)"),
    29: _L("OBD Brasil fase 2 (OBDBr-2)", "Brazil OBD phase 2 (OBDBr-2)"),
    30: _L("KOBD (Coreia)", "KOBD (Korea)"), 31: _L("IOBD I (Índia)", "IOBD I (India)"), 32: _L("IOBD II (Índia)", "IOBD II (India)"),
}


def _fuel_status(d: bytes):
    return FUEL_SYSTEM.get(d[0], "—") if d[0] else "—"


def _fuel_type(d: bytes):
    return FUEL_TYPES.get(d[0], str(d[0]))


PIDS: List[PID] = [
    PID(0x03, "fuel_status", _L("Controle de combustível", "Fuel system status"), "", 2, _fuel_status, 4.0),
    PID(0x04, "load", _L("Carga do motor", "Engine load"), "%", 1, _pct, 0.8),
    PID(0x05, "ect", _L("Temperatura do motor", "Coolant temperature"), "°C", 1, _temp, 2.0),
    PID(0x06, "stft1", _L("Correção curta de combustível (banco 1)", "Short-term fuel trim (bank 1)"), "%", 1, _trim, 1.5, 1),
    PID(0x07, "ltft1", _L("Correção longa de combustível (banco 1)", "Long-term fuel trim (bank 1)"), "%", 1, _trim, 3.0, 1),
    PID(0x08, "stft2", _L("Correção curta de combustível (banco 2)", "Short-term fuel trim (bank 2)"), "%", 1, _trim, 3.0, 1),
    PID(0x09, "ltft2", _L("Correção longa de combustível (banco 2)", "Long-term fuel trim (bank 2)"), "%", 1, _trim, 5.0, 1),
    PID(0x0A, "fuel_press", _L("Pressão do combustível", "Fuel pressure"), "kPa", 1, lambda d: d[0] * 3, 4.0),
    PID(0x0B, "map", _L("Pressão do coletor (MAP)", "Intake manifold pressure (MAP)"), "kPa", 1, lambda d: d[0], 1.0),
    PID(0x0C, "rpm", _L("Rotação do motor", "Engine speed"), "rpm", 2, lambda d: _ab(d) / 4.0, 0.3),
    PID(0x0D, "speed", _L("Velocidade", "Vehicle speed"), "km/h", 1, lambda d: d[0], 0.4),
    PID(0x0E, "timing", _L("Avanço da ignição", "Ignition timing advance"), "°", 1, lambda d: d[0] / 2.0 - 64, 1.2, 1),
    PID(0x0F, "iat", _L("Temperatura do ar admitido", "Intake air temperature"), "°C", 1, _temp, 3.0),
    PID(0x10, "maf", _L("Fluxo de ar (MAF)", "Mass air flow (MAF)"), "g/s", 2, lambda d: _ab(d) / 100.0, 1.0, 1),
    PID(0x11, "throttle", _L("Posição do acelerador", "Throttle position"), "%", 1, _pct, 0.5),
    PID(0x14, "o2_b1s1", _L("Sensor O2 B1-S1 (tensão)", "O2 sensor B1-S1 (voltage)"), "V", 2, lambda d: d[0] / 200.0, 1.5, 2),
    PID(0x15, "o2_b1s2", _L("Sensor O2 B1-S2 pós-catalisador (tensão)", "O2 sensor B1-S2, post-catalyst (voltage)"), "V", 2, lambda d: d[0] / 200.0, 2.0, 2),
    PID(0x1F, "runtime", _L("Tempo desde a partida", "Time since engine start"), "s", 2, _ab, 5.0),
    PID(0x21, "dist_mil", _L("Distância rodada com a luz acesa", "Distance driven with the MIL on"), "km", 2, _ab, 15.0),
    PID(0x24, "lambda_b1s1", _L("Sensor A/F B1-S1 (lambda)", "A/F sensor B1-S1 (lambda)"), "λ", 4, _lambda, 1.0, 3),
    PID(0x2C, "egr_cmd", _L("EGR comandada", "Commanded EGR"), "%", 1, _pct, 5.0),
    PID(0x2D, "egr_err", _L("Erro da EGR", "EGR error"), "%", 1, _trim, 5.0, 1),
    PID(0x2E, "evap_purge", _L("Purga do canister (EVAP)", "Commanded evaporative purge"), "%", 1, _pct, 3.0),
    PID(0x2F, "fuel_level", _L("Nível de combustível", "Fuel level"), "%", 1, _pct, 10.0),
    PID(0x30, "warmups", _L("Aquecimentos desde que apagou os códigos", "Warm-ups since codes cleared"), "", 1, lambda d: d[0], 20.0),
    PID(0x31, "dist_clear", _L("Km desde que apagou os códigos", "Distance since codes cleared"), "km", 2, _ab, 20.0),
    PID(0x33, "baro", _L("Pressão barométrica", "Barometric pressure"), "kPa", 1, lambda d: d[0], 10.0),
    PID(0x3C, "cat_temp", _L("Temperatura do catalisador (B1-S1)", "Catalyst temperature (B1-S1)"), "°C", 2, lambda d: _ab(d) / 10.0 - 40, 5.0),
    PID(0x42, "volt", _L("Tensão do sistema (ECU)", "Control module voltage (ECU)"), "V", 2, lambda d: _ab(d) / 1000.0, 2.5, 2),
    PID(0x43, "abs_load", _L("Carga absoluta", "Absolute load"), "%", 2, lambda d: _ab(d) * 100.0 / 255, 3.0),
    PID(0x44, "cmd_lambda", _L("Lambda comandado", "Commanded lambda"), "λ", 2, _lambda, 2.0, 3),
    PID(0x45, "rel_throttle", _L("Acelerador relativo", "Relative throttle position"), "%", 1, _pct, 3.0),
    PID(0x46, "ambient", _L("Temperatura ambiente", "Ambient air temperature"), "°C", 1, _temp, 10.0),
    PID(0x49, "pedal_d", _L("Pedal do acelerador (D)", "Accelerator pedal position D"), "%", 1, _pct, 1.0),
    PID(0x4A, "pedal_e", _L("Pedal do acelerador (E)", "Accelerator pedal position E"), "%", 1, _pct, 1.5),
    PID(0x4C, "cmd_throttle", _L("Acelerador comandado", "Commanded throttle actuator"), "%", 1, _pct, 1.0),
    PID(0x4D, "time_mil", _L("Tempo rodado com a luz acesa", "Time run with the MIL on"), "min", 2, _ab, 30.0),
    PID(0x4E, "time_clear", _L("Tempo desde que apagou os códigos", "Time since codes cleared"), "min", 2, _ab, 30.0),
    PID(0x51, "fuel_type", _L("Tipo de combustível", "Fuel type"), "", 1, _fuel_type, 60.0),
    PID(0x52, "ethanol", _L("Etanol no combustível", "Ethanol fuel percentage"), "%", 1, _pct, 30.0),
    PID(0x5C, "oil_temp", _L("Temperatura do óleo", "Engine oil temperature"), "°C", 1, _temp, 5.0),
    PID(0x5E, "fuel_rate", _L("Consumo instantâneo", "Engine fuel rate"), "L/h", 2, lambda d: _ab(d) / 20.0, 1.5, 2),
]

PID_BY_NUM: Dict[int, PID] = {p.pid: p for p in PIDS}
PID_BY_KEY: Dict[str, PID] = {p.key: p for p in PIDS}

# PIDs read for the "freeze frame" (mode 02) when available
FREEZE_PIDS = [0x0C, 0x0D, 0x05, 0x04, 0x0B, 0x11, 0x06, 0x07, 0x0E, 0x0F, 0x03, 0x42, 0x43, 0x24]


def decode_pid(pid: int, data: bytes):
    p = PID_BY_NUM.get(pid)
    if p is None:
        return None
    if len(data) < p.nbytes:
        raise ValueError(f"PID {pid:02X}: expected {p.nbytes} byte(s), got {len(data)}")
    return p.fn(data)


# ------------------------------------------------------------------ DTC


def decode_dtc(b1: int, b2: int) -> Optional[str]:
    if b1 == 0 and b2 == 0:
        return None
    letter = "PCBU"[(b1 >> 6) & 3]
    return f"{letter}{(b1 >> 4) & 3}{b1 & 0xF:X}{b2 >> 4:X}{b2 & 0xF:X}"


def _pairs(body: bytes) -> List[str]:
    out = []
    for i in range(0, len(body) - 1, 2):
        c = decode_dtc(body[i], body[i + 1])
        if c:
            out.append(c)
    return out


def decode_dtc_payload(data: bytes, can: bool) -> List[str]:
    """Decode the answer to modes 03/07/0A (data[0] = 0x43/0x47/0x4A).

    On CAN the answer carries one byte with the number of codes right after the SID; on J1850 / ISO 9141 /
    KWP it does not (3 codes per message, padded with 00 00).
    """
    body = bytes(data[1:])
    if not body:
        return []
    if can:
        with_count = _pairs(body[1:])
        if body[0] == len(with_count):
            return with_count
        without = _pairs(body)
        if len(body) % 2 == 1:
            return with_count
        return without
    return _pairs(body)


# ----------------------------------------------------- readiness monitors

_CONT = [(_L("Falha de ignição (misfire)", "Misfire"), 0, 4),
         (_L("Sistema de combustível", "Fuel system"), 1, 5),
         (_L("Componentes (abrangente)", "Comprehensive components"), 2, 6)]
_NONCONT_SPARK = [
    (_L("Catalisador", "Catalyst"), 0), (_L("Catalisador aquecido", "Heated catalyst"), 1),
    (_L("Sistema EVAP (vapores)", "EVAP system (fuel vapors)"), 2), (_L("Ar secundário", "Secondary air"), 3),
    (_L("Ar-condicionado (refrigerante)", "A/C refrigerant"), 4), (_L("Sensor de oxigênio / A-F", "Oxygen / A-F sensor"), 5),
    (_L("Aquecedor do sensor de O2", "O2 sensor heater"), 6), (_L("EGR / VVT", "EGR / VVT"), 7),
]
_NONCONT_DIESEL = [
    (_L("Catalisador NMHC", "NMHC catalyst"), 0), (_L("Pós-tratamento NOx/SCR", "NOx/SCR aftertreatment"), 1),
    (_L("Pressão de turbo", "Boost pressure"), 3), (_L("Sensor de gases de escape", "Exhaust gas sensor"), 5),
    (_L("Filtro de partículas (DPF)", "Particulate filter (DPF)"), 6), (_L("EGR / VVT", "EGR / VVT"), 7),
]


def decode_monitor_status(a: int, b: int, c: int, d: int) -> dict:
    """PID 01 (and 41): MIL, number of DTCs and the state of the readiness monitors."""
    spark = not (b & 0x08)
    mons = []
    for name, sup_bit, inc_bit in _CONT:
        sup = bool(b & (1 << sup_bit))
        mons.append({"name": name, "supported": sup, "ready": sup and not (b & (1 << inc_bit)), "continuous": True})
    for name, bit in (_NONCONT_SPARK if spark else _NONCONT_DIESEL):
        sup = bool(c & (1 << bit))
        mons.append({"name": name, "supported": sup, "ready": sup and not (d & (1 << bit)), "continuous": False})
    return {
        "mil": bool(a & 0x80),
        "count": a & 0x7F,
        "ignition": "spark" if spark else "diesel",
        "monitors": [m for m in mons if m["supported"]],
    }


# ------------------------------------------------------ negative responses

NRC_TEXT: Dict[int, Msg] = {
    0x10: _L("Recusado pela central (erro geral).", "Rejected by the ECU (general reject)."),
    0x11: _L("Serviço não suportado por esta central.", "Service not supported by this ECU."),
    0x12: _L("Função não suportada por esta central.", "Sub-function not supported by this ECU."),
    0x13: _L("Formato da mensagem incorreto.", "Incorrect message length or format."),
    0x21: _L("Central ocupada, tente novamente.", "ECU busy, try again."),
    0x22: _L("Condições incorretas: ligue a ignição (chave em ON) e, de preferência, desligue o motor.",
             "Conditions not correct: turn the ignition on (key to ON) and, preferably, switch the engine off."),
    0x31: _L("Valor fora da faixa.", "Request out of range."),
    0x78: _L("Central ainda processando (resposta pendente).", "ECU still processing (response pending)."),
}


def nrc_msg(nrc: int) -> Msg:
    return NRC_TEXT.get(nrc) or _L(f"Resposta negativa da central (código 0x{nrc:02X}).",
                                    f"Negative response from the ECU (code 0x{nrc:02X}).")
