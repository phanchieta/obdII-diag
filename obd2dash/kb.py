"""Trouble-code (DTC) knowledge base, bilingual (pt / en).

Lookup order for each code:
  1. kb/curated.<lang>.txt   hand-written guidance (editable plain text)
  2. generated families      O2 sensors, cylinders, coils, injectors, MAP/IAT/ECT/TPS, shift solenoids
  3. kb/sae_titles.en.txt    generic SAE titles in English (fallback)
  4. generic guidance based on the code prefix

Brand-specific content (Honda tips and Honda P1xxx codes) is only used when the vehicle name contains "Honda".
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Dict, List, Optional

from .i18n import LANGS, Msg

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")


def B(pt, en) -> Dict[str, object]:
    """Bilingual value (string or list)."""
    return {"pt": pt, "en": en}


SEVERITY_TEXT = {1: B("Baixa", "Low"), 2: B("Média", "Medium"), 3: B("Alta", "High"), 4: B("Crítica", "Critical")}
DRIVE_DEFAULT = {
    1: B("Sim. Não há risco imediato para o carro; corrija quando puder.",
         "Yes. There is no immediate risk for the car; fix it when you can."),
    2: B("Sim, mas corrija em breve para evitar consumo alto, poluição ou danos maiores.",
         "Yes, but fix it soon to avoid high fuel consumption, pollution or bigger damage."),
    3: B("Evite rodar: leve a uma oficina o quanto antes, em trajetos curtos e sem acelerar forte.",
         "Avoid driving: take it to a workshop as soon as possible, short trips only and without heavy throttle."),
    4: B("NÃO dirija. Pare em local seguro e chame socorro ou oficina.",
         "DO NOT drive. Stop in a safe place and call roadside assistance or a workshop."),
}
AREA = {
    "P": B("Motor e câmbio (powertrain)", "Engine and transmission (powertrain)"),
    "C": B("Chassi (freios, ABS, suspensão)", "Chassis (brakes, ABS, suspension)"),
    "B": B("Carroceria (airbag, conforto)", "Body (airbags, comfort)"),
    "U": B("Rede de comunicação entre módulos", "Network (communication between modules)"),
}
SCOPE = {
    True: B("Genérico (padrão SAE, vale para qualquer marca)", "Generic (SAE standard, the same for every brand)"),
    False: B("Específico do fabricante (o significado pode variar por marca, modelo e ano)",
             "Manufacturer-specific (the meaning can vary by brand, model and year)"),
}
FAMILY_GENERIC = {
    "P00": B("Controle auxiliar de ar e combustível (comando variável, válvulas, aquecedores)",
             "Auxiliary air and fuel control (variable valve timing, valves, heaters)"),
    "P01": B("Medição de ar e combustível (sensores de ar, temperatura e oxigênio)",
             "Air and fuel metering (air, temperature and oxygen sensors)"),
    "P02": B("Injetores e sistema de combustível", "Injectors and fuel system"),
    "P03": B("Ignição, falha de ignição e sensores de rotação", "Ignition, misfires and crank/cam sensors"),
    "P04": B("Controles de emissões (EGR, EVAP, catalisador, ar secundário)",
             "Emission controls (EGR, EVAP, catalyst, secondary air)"),
    "P05": B("Velocidade do veículo, marcha lenta e entradas auxiliares",
             "Vehicle speed, idle control and auxiliary inputs"),
    "P06": B("Central eletrônica (ECU) e circuitos de saída", "Control module (ECU) and output circuits"),
    "P07": B("Câmbio / transmissão", "Transmission"),
    "P08": B("Câmbio / transmissão", "Transmission"),
    "P09": B("Câmbio / transmissão", "Transmission"),
    "P0A": B("Sistemas híbridos / elétricos", "Hybrid / electric systems"),
    "P1": B("Específico do fabricante", "Manufacturer-specific"),
    "P2": B("Sensores e atuadores (genérico SAE)", "Sensors and actuators (generic SAE)"),
    "P3": B("Ignição / específico do fabricante", "Ignition / manufacturer-specific"),
    "U0": B("Comunicação entre módulos (rede CAN)", "Communication between modules (CAN bus)"),
    "U1": B("Comunicação entre módulos (específico do fabricante)", "Communication between modules (manufacturer-specific)"),
    "U2": B("Comunicação entre módulos (genérico)", "Communication between modules (generic)"),
}
ACTIONS_GENERIC = B(
    ["Anote o código e veja se existem outros junto com ele: vários códigos juntos costumam ter uma causa comum",
     "Use o botão de pesquisa abaixo (precisa de internet) ou copie o relatório do app e cole no chat para ajuda",
     "Se o carro anda normal, a falha pode ter sido pontual: apague uma vez e veja se volta",
     "Evite trocar peças só pelo código: ele aponta um circuito ou sistema, não necessariamente a peça com defeito"],
    ["Write the code down and check whether other codes appear together with it: several codes at once usually share a cause",
     "Use the search button below (needs internet) or copy the report and paste it into a chat or forum for help",
     "If the car drives normally the fault may have been a one-off: clear once and see whether it comes back",
     "Avoid replacing parts just because of a code: it points to a circuit or system, not necessarily to the faulty part"])
NOTE_SAE = B(
    'Este código ainda não tem texto em português na base do app. A descrição acima é a definição padrão (em inglês). '
    'Use "Copiar relatório" e cole no chat para uma explicação completa.',
    'This code has no detailed guidance in the app database yet; the title is the standard SAE definition. '
    'Use "Copy report" and paste it into a chat or forum for a full explanation.')
WHAT_SAE = B("Descrição genérica (SAE) em inglês: ainda não há texto em português para este código.",
             "Generic SAE definition: this code has no detailed guidance in the built-in database yet.")
WHAT_DEFAULT = B("A central do carro registrou o seguinte problema: {title}.",
                 "The car's control module logged the following problem: {title}.")
GENERIC_TITLE = B("Código sem descrição na base offline", "Code not in the offline database")
GENERIC_WHAT = B("Este código não está na base do app. {fam}", "This code is not in the app database. {fam}")
GENERIC_FAM = B("Ele pertence à família: {fam}.", "It belongs to the family: {fam}.")
SEARCH_Q = B("{car} código {code} OBD2", "{car} {code} OBD2 trouble code")

_HEADER = re.compile(r"^([PCBU][0-9A-F]{4})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([1-4])\s*$")


def _split(s: str) -> List[str]:
    return [p.strip() for p in s.split(";") if p.strip()]


def parse_curated(text: str) -> Dict[str, dict]:
    """Parse a curated file: ``CODE | title | system | severity`` followed by e:/r:/c:/a:/h: lines."""
    out: Dict[str, dict] = {}
    cur: Optional[dict] = None
    code = None
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if not line:
            if code and cur:
                out[code] = cur
            code, cur = None, None
            continue
        if line.startswith("#"):
            continue
        m = _HEADER.match(line)
        if m:
            code = m.group(1)
            cur = {"title": m.group(2), "system": m.group(3), "severity": int(m.group(4)),
                   "e": "", "r": "", "c": [], "a": [], "h": ""}
            continue
        if cur is not None and re.match(r"^[ercah]:\s", line):
            k, v = line[0], line[2:].strip()
            cur[k] = _split(v) if k in "ca" else v
    return out


# ------------------------------------------------------------------- families

# (name pt, name en, system pt, system en, severity, causes pt, causes en, actions pt, actions en)
_SENSORS = {
    100: ("Sensor de fluxo de ar (MAF)", "Mass air flow (MAF) sensor", "Admissão de ar", "Air intake", 2,
          ["Sensor MAF sujo ou defeituoso", "Entrada falsa de ar depois do sensor", "Filtro de ar entupido ou com óleo"],
          ["Dirty or faulty MAF sensor", "Air leak after the sensor", "Clogged or oily air filter"],
          ["Inspecione o filtro de ar e as mangueiras de admissão",
           "Limpe o sensor só com limpa-contato próprio para MAF, sem tocar no elemento"],
          ["Inspect the air filter and the intake hoses", "Clean the sensor only with MAF-specific cleaner, without touching the element"]),
    105: ("Sensor de pressão do coletor (MAP)", "Manifold absolute pressure (MAP) sensor", "Admissão de ar", "Air intake", 2,
          ["Mangueira de vácuo do sensor solta, rachada ou entupida", "Sensor MAP com defeito", "Entrada falsa de ar"],
          ["Sensor vacuum hose loose, cracked or clogged", "Faulty MAP sensor", "Air leak"],
          ["Verifique a mangueira e a conexão do sensor ao coletor",
           "No Painel: com a ignição ligada e o motor desligado a pressão do coletor deve ser parecida com a atmosférica "
           "(cerca de 90 a 100 kPa); em marcha lenta, entre 25 e 45 kPa"],
          ["Check the hose and the connection between the sensor and the manifold",
           "On the Dashboard: with the ignition on and the engine off the manifold pressure should be close to atmospheric "
           "(about 90 to 100 kPa); at idle, between 25 and 45 kPa"]),
    110: ("Sensor de temperatura do ar admitido (IAT)", "Intake air temperature (IAT) sensor", "Admissão de ar", "Air intake", 1,
          ["Sensor de temperatura do ar com defeito", "Conector com mau contato"],
          ["Faulty air temperature sensor", "Connector with poor contact"],
          ["Com o motor frio, compare no Painel a temperatura do ar admitido com a do ambiente: devem ser parecidas"],
          ["With a cold engine, compare the intake air temperature on the Dashboard with the ambient temperature: they should be similar"]),
    115: ("Sensor de temperatura do motor (ECT)", "Engine coolant temperature (ECT) sensor", "Arrefecimento", "Cooling", 2,
          ["Sensor de temperatura do motor com defeito", "Conector oxidado", "Termostato (se a temperatura não sobe)"],
          ["Faulty coolant temperature sensor", "Corroded connector", "Thermostat (if the temperature does not rise)"],
          ["Compare a temperatura do Painel com o ponteiro do carro", "Com o motor frio ela deve ser parecida com a temperatura ambiente"],
          ["Compare the Dashboard temperature with the car's gauge", "With a cold engine it should be close to the ambient temperature"]),
    120: ("Sensor de posição da borboleta / pedal (A)", "Throttle / pedal position sensor (A)", "Acelerador", "Throttle", 3,
          ["Sensor de posição com defeito ou sujo", "Corpo de borboleta com carvão", "Fiação do sensor"],
          ["Faulty or dirty position sensor", "Carbon build-up in the throttle body", "Sensor wiring"],
          ["Limpe o corpo de borboleta", "No Painel, veja se o acelerador vai de ~0–15% (solto) até ~100% de forma suave, sem saltos"],
          ["Clean the throttle body", "On the Dashboard, check that the throttle goes from ~0–15% (released) to ~100% smoothly, without jumps"]),
    190: ("Sensor de pressão do trilho de combustível", "Fuel rail pressure sensor", "Combustível", "Fuel", 2,
          ["Sensor de pressão", "Bomba de combustível fraca", "Filtro de combustível entupido"],
          ["Pressure sensor", "Weak fuel pump", "Clogged fuel filter"],
          ["Teste a pressão do combustível com manômetro"], ["Test the fuel pressure with a gauge"]),
    195: ("Sensor de temperatura do óleo do motor", "Engine oil temperature sensor", "Lubrificação", "Lubrication", 1,
          ["Sensor de temperatura do óleo", "Fiação"], ["Oil temperature sensor", "Wiring"],
          ["Verifique o conector do sensor"], ["Check the sensor connector"]),
    220: ("Sensor de posição da borboleta / pedal (B)", "Throttle / pedal position sensor (B)", "Acelerador", "Throttle", 3,
          ["Sensor de posição com defeito ou sujo", "Corpo de borboleta com carvão", "Fiação do sensor"],
          ["Faulty or dirty position sensor", "Carbon build-up in the throttle body", "Sensor wiring"],
          ["Limpe o corpo de borboleta", "Compare com o sensor A: os dois devem andar juntos"],
          ["Clean the throttle body", "Compare with sensor A: both should move together"]),
}
# (desc pt, desc en, causes pt, causes en, actions pt, actions en)
_SUFFIXES = [
    ("circuito com defeito", "circuit malfunction",
     ["Conector solto ou oxidado", "Fiação rompida ou em curto"], ["Loose or corroded connector", "Broken or shorted wiring"],
     ["Inspecione o conector e a fiação do sensor"], ["Inspect the connector and wiring of the sensor"]),
    ("desempenho fora do esperado", "performance outside the expected range",
     ["Sensor com leitura lenta ou incoerente"], ["Sensor with a slow or inconsistent reading"],
     ["Compare a leitura no Painel com o esperado para a situação"],
     ["Compare the reading on the Dashboard with what is expected for the situation"]),
    ("sinal baixo", "low signal",
     ["Fiação em curto com o terra", "Sensor em curto"], ["Wiring shorted to ground", "Shorted sensor"],
     ["Desligue a ignição e verifique o conector e a fiação"], ["Switch the ignition off and check the connector and wiring"]),
    ("sinal alto", "high signal",
     ["Fio rompido ou desconectado", "Falta de terra do sensor", "Sensor aberto"],
     ["Broken or disconnected wire", "Missing sensor ground", "Open sensor"],
     ["Desligue a ignição e verifique o conector e a fiação"], ["Switch the ignition off and check the connector and wiring"]),
    ("sinal intermitente", "intermittent signal",
     ["Mau contato no conector (o sinal vai e volta)"], ["Poor connector contact (the signal comes and goes)"],
     ["Balance a fiação com o motor ligado para provocar a falha"], ["Wiggle the wiring with the engine running to provoke the fault"]),
]
_O2_STARTS = {130: (1, 1), 136: (1, 2), 142: (1, 3), 150: (2, 1), 156: (2, 2), 162: (2, 3)}
_O2_POS = {1: ("antes do catalisador", "before the catalyst"), 2: ("depois do catalisador", "after the catalyst"),
           3: ("terceiro sensor", "third sensor")}
_O2_SUFFIXES = [
    ("circuito com defeito", "circuit malfunction",
     ["Sensor de oxigênio com defeito", "Conector ou fiação"], ["Faulty oxygen sensor", "Connector or wiring"],
     ["Inspecione conector e fiação", "Se estiver bom, troque o sensor"], ["Inspect the connector and wiring", "If they are fine, replace the sensor"]),
    ("sinal baixo", "low voltage",
     ["Mistura pobre real (veja P0171)", "Sensor em curto ou envelhecido", "Fiação"],
     ["Genuinely lean mixture (see P0171)", "Shorted or aged sensor", "Wiring"],
     ["Veja se há P0171 junto e resolva primeiro", "Verifique conector e fiação"],
     ["Check whether P0171 is also present and fix it first", "Check the connector and wiring"]),
    ("sinal alto", "high voltage",
     ["Mistura rica real (veja P0172)", "Fio solto ou em curto com 12 V", "Sensor"],
     ["Genuinely rich mixture (see P0172)", "Loose wire or short to 12 V", "Sensor"],
     ["Veja se há P0172 junto e resolva primeiro", "Verifique conector e fiação"],
     ["Check whether P0172 is also present and fix it first", "Check the connector and wiring"]),
    ("resposta lenta", "slow response",
     ["Sensor envelhecido (causa mais comum, principalmente acima de 100 mil km)", "Vazamento no escapamento",
      "Combustível adulterado", "Fiação"],
     ["Aged sensor (most common cause, especially above 100,000 km / 60,000 miles)", "Exhaust leak", "Contaminated fuel", "Wiring"],
     ["Verifique vazamentos no escapamento", "Se o sensor tem muitos anos, a troca costuma resolver"],
     ["Check for exhaust leaks", "If the sensor is old, replacing it usually fixes it"]),
    ("sem atividade detectada", "no activity detected",
     ["Sensor morto", "Fio rompido ou conector solto"], ["Dead sensor", "Broken wire or loose connector"],
     ["Verifique conector e fiação", "Troque o sensor"], ["Check the connector and wiring", "Replace the sensor"]),
    ("aquecedor do sensor com defeito", "sensor heater circuit malfunction",
     ["Fusível do aquecedor", "Resistência interna do sensor aberta", "Fiação do aquecedor"],
     ["Heater fuse", "Open internal heater element", "Heater wiring"],
     ["Verifique o fusível do aquecedor do sensor", "O aquecedor é interno ao sensor: se o fusível está bom, troque o sensor"],
     ["Check the sensor heater fuse", "The heater is inside the sensor: if the fuse is good, replace the sensor"]),
]
_O2_HONDA = ("Na Honda o sensor dianteiro (S1) costuma ser do tipo A/F (faixa larga); o traseiro (S2) é um sensor de oxigênio comum.",
             "On Hondas the front sensor (S1) is usually a wide-range A/F sensor; the rear one (S2) is a regular oxygen sensor.")


def _entry(title, system, severity, e="", r="", c=(), a=(), h=""):
    return {"title": title, "system": system, "severity": severity, "e": e, "r": r, "c": list(c), "a": list(a), "h": h}


def _bi(fn) -> Dict[str, dict]:
    """Build an entry for every language: fn(i) -> entry, with i = 0 (pt) / 1 (en)."""
    return {lang: fn(i) for i, lang in enumerate(LANGS)}


_COMBOS = [
    (lambda c: any(re.fullmatch(r"P030[0-9]|P031[0-2]", x) for x in c) and any(x in c for x in ("P0420", "P0430")),
     B("Há falha de ignição e eficiência do catalisador no mesmo diagnóstico. Resolva primeiro a falha de ignição: queima incompleta danifica o catalisador e costuma ser a causa do P0420.",
       "There is a misfire AND a catalyst-efficiency code at the same time. Fix the misfire first: incomplete combustion damages the catalytic converter and is the usual cause of P0420.")),
    (lambda c: any(re.fullmatch(r"P030[0-9]|P031[0-2]", x) for x in c) and any(x in c for x in ("P0171", "P0174")),
     B("Mistura pobre pode causar falha de ignição. Procure entrada falsa de ar e confira a pressão de combustível antes de trocar velas e bobinas.",
       "A lean mixture can cause misfires. Look for air leaks and check the fuel pressure before replacing spark plugs and coils.")),
    (lambda c: "P0171" in c and "P0174" in c,
     B("Os dois bancos estão com mistura pobre: a causa provavelmente é comum aos dois (medidor de ar, pressão de combustível ou entrada falsa de ar).",
       "Both banks are running lean: the cause is probably common to both (air flow meter, fuel pressure or an air leak).")),
    (lambda c: any(re.fullmatch(r"P04(4[0-9]|5[0-7])|P145[67]", x) for x in c),
     B("Códigos de EVAP: comece pela tampa do tanque. Aperte até ouvir cliques ou troque-a. É a causa mais comum e mais barata.",
       "EVAP codes: start with the fuel cap. Tighten it until it clicks or replace it. It is the most common and cheapest cause.")),
    (lambda c: any(x.startswith("U") for x in c),
     B("Códigos de comunicação (U) podem aparecer quando a bateria está fraca ou foi desligada. Confira bateria, terminais e fusíveis antes de suspeitar de módulos.",
       "Communication (U) codes can appear when the battery is weak or was disconnected. Check the battery, terminals and fuses before suspecting modules.")),
    (lambda c: any(x in c for x in ("P0562", "P0560", "P1297", "P1298")) and len(c) > 1,
     B("Tensão baixa ou instável causa códigos 'fantasma' em vários sensores. Teste bateria e alternador antes de trocar peças.",
       "Low or unstable voltage causes 'ghost' codes in several sensors. Test the battery and alternator before replacing parts.")),
    (lambda c: any(x in c for x in ("P2646", "P2647", "P1259")),
     B("Em códigos de VTEC / comando variável, confira nível e estado do óleo antes de qualquer outra coisa.",
       "For VTEC / variable valve actuation codes, check the oil level and condition before anything else.")),
    (lambda c: len(c) >= 4,
     B("Muitos códigos ao mesmo tempo normalmente têm uma causa comum (bateria, terra, fusível, rede). Evite trocar peças por código.",
       "Many codes at once usually have a common cause (battery, ground, fuse, network). Avoid replacing parts based on codes alone.")),
]


class KnowledgeBase:
    def __init__(self, car_label: str = ""):
        self.car_label = ""
        self.honda = False
        self.set_car(car_label)
        self.curated: Dict[str, Dict[str, dict]] = {}
        for lang in LANGS:
            path = os.path.join(_DIR, f"curated.{lang}.txt")
            with open(path, encoding="utf-8") as f:
                self.curated[lang] = parse_curated(f.read())
        self._sae: Optional[Dict[str, str]] = None

    def set_car(self, car_label: str) -> None:
        self.car_label = (car_label or "").strip()
        self.honda = "honda" in self.car_label.lower()

    # ---------------------------------------------------------- SAE titles
    def _sae_titles(self) -> Dict[str, str]:
        if self._sae is None:
            self._sae = {}
            try:
                with open(os.path.join(_DIR, "sae_titles.en.txt"), encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r"^([PCBU][0-9A-F]{4}) - (.+)$", line.strip())
                        if m:
                            self._sae[m.group(1)] = m.group(2)
            except OSError:
                pass
        return self._sae

    # ------------------------------------------------------------- curated
    def _curated(self, code: str) -> Optional[Dict[str, dict]]:
        found = {lang: self.curated[lang].get(code) for lang in LANGS}
        if not any(found.values()):
            return None
        first = next(e for e in found.values() if e)
        if first["system"].startswith("Honda") and not self.honda:
            return None                      # brand-specific code of another manufacturer
        return {lang: (e or first) for lang, e in found.items()}

    # ------------------------------------------------------------ families
    def _family(self, code: str) -> Optional[Dict[str, dict]]:
        m = re.fullmatch(r"P03(0[1-9]|1[0-2])", code)
        if m:
            n = int(m.group(1))
            base = {lang: self.curated[lang].get("P0300") for lang in LANGS}
            if all(base.values()):
                def misfire(i):
                    b = base[LANGS[i]]
                    e = (f"A central detectou que o cilindro {n} não queimou direito a mistura (o motor 'falha' ou 'engasga'). Se a luz piscar, a falha é forte.",
                         f"The ECU detected that cylinder {n} did not burn the mixture properly (the engine 'misfires' or 'stumbles'). If the light flashes, the misfire is severe.")[i]
                    first = (f"Troque a bobina do cilindro {n} de lugar com a de outro cilindro: se o código mudar de cilindro, a bobina é a culpada",
                             f"Swap the coil of cylinder {n} with the one of another cylinder: if the code moves to that cylinder, the coil is the culprit")[i]
                    second = (f"Troque a vela do cilindro {n} e inspecione o estado das demais",
                              f"Replace the spark plug of cylinder {n} and inspect the condition of the others")[i]
                    return _entry((f"Falha de ignição no cilindro {n}", f"Misfire on cylinder {n}")[i], b["system"], b["severity"],
                                  e, b["r"], b["c"], [first, second] + b["a"][1:], b["h"])
                return _bi(misfire)
        if re.fullmatch(r"P0[12][0-9]{2}", code):
            n = int(code[1:])
            for start, (npt, nen, spt, sen, sev, cpt, cen, apt, aen) in _SENSORS.items():
                if start <= n < start + 5:
                    dpt, den, c2pt, c2en, a2pt, a2en = _SUFFIXES[n - start]
                    return _bi(lambda i: _entry(
                        (f"{npt}: {dpt}", f"{nen}: {den}")[i], (spt, sen)[i], sev,
                        (f"A central detectou um problema no {npt}: {dpt}.", f"The ECU detected a problem with the {nen}: {den}.")[i],
                        "", ((c2pt + cpt), (c2en + cen))[i], ((a2pt + apt), (a2en + aen))[i]))
        if re.fullmatch(r"P01[3-6][0-9]", code):
            n = int(code[1:])
            for start, (bank, sens) in _O2_STARTS.items():
                if start <= n < start + 6:
                    dpt, den, cpt, cen, apt, aen = _O2_SUFFIXES[n - start]
                    ppt, pen = _O2_POS[sens]
                    tip = _O2_HONDA if sens in (1, 2) else ("", "")
                    return _bi(lambda i: _entry(
                        (f"Sensor de oxigênio, banco {bank} sensor {sens} ({ppt}): {dpt}",
                         f"Oxygen sensor, bank {bank} sensor {sens} ({pen}): {den}")[i],
                        ("Sensor de oxigênio", "Oxygen sensor")[i], 2,
                        (f"O sensor de oxigênio do banco {bank}, posição {sens} ({ppt}) apresenta: {dpt}. Ele informa à central se a mistura está certa e se o catalisador funciona.",
                         f"The oxygen sensor of bank {bank}, position {sens} ({pen}) reports: {den}. It tells the ECU whether the mixture is right and whether the catalyst works.")[i],
                        "", (cpt, cen)[i], (apt, aen)[i], tip[i]))
        m = re.fullmatch(r"P02(0[1-9]|1[0-2])", code)
        if m:
            n = int(m.group(1))
            return _bi(lambda i: _entry(
                (f"Circuito do injetor do cilindro {n}", f"Cylinder {n} injector circuit")[i],
                ("Injeção de combustível", "Fuel injection")[i], 3,
                (f"A central detectou problema elétrico no injetor do cilindro {n}; o cilindro pode ficar sem combustível e falhar.",
                 f"The ECU detected an electrical problem in the cylinder {n} injector; the cylinder may run without fuel and misfire.")[i],
                "", (["Injetor com bobina aberta ou em curto", "Conector do injetor", "Fiação", "Central (raro)"],
                     ["Injector with open or shorted coil", "Injector connector", "Wiring", "ECU (rare)"])[i],
                (["Verifique o conector do injetor", "Meça a resistência do injetor (valor do manual)",
                  "Se há falha de ignição no mesmo cilindro (P030x), suspeite do injetor"],
                 ["Check the injector connector", "Measure the injector resistance (value in the service manual)",
                  "If there is a misfire on the same cylinder (P030x), suspect the injector"])[i]))
        if re.fullmatch(r"P02[6-9][0-9]", code) and 261 <= int(code[1:]) <= 296:
            k = int(code[1:]) - 261
            cyl = k // 3 + 1
            kinds = (("sinal baixo", "sinal alto", "contribuição/equilíbrio do cilindro"),
                     ("low signal", "high signal", "cylinder contribution/balance"))
            return _bi(lambda i: _entry(
                (f"Injetor do cilindro {cyl}: {kinds[0][k % 3]}", f"Cylinder {cyl} injector: {kinds[1][k % 3]}")[i],
                ("Injeção de combustível", "Fuel injection")[i], 3,
                (f"Problema no injetor do cilindro {cyl} ({kinds[0][k % 3]}).", f"Problem with the cylinder {cyl} injector ({kinds[1][k % 3]}).")[i],
                "", (["Injetor", "Fiação ou conector", "Pressão de combustível"], ["Injector", "Wiring or connector", "Fuel pressure"])[i],
                (["Verifique o conector e a fiação do injetor", "Limpeza ou teste do injetor"],
                 ["Check the injector connector and wiring", "Clean or test the injector"])[i]))
        m = re.fullmatch(r"P03(5[1-9]|6[0-2])", code)
        if m:
            k = int(code[1:]) - 351
            letter = "ABCDEFGHIJKL"[k]
            return _bi(lambda i: _entry(
                (f"Circuito da bobina de ignição {letter} (primário/secundário)", f"Ignition coil {letter} primary/secondary circuit")[i],
                ("Ignição", "Ignition")[i], 3,
                (f"Problema elétrico na bobina de ignição {letter} (normalmente associada ao cilindro {k + 1}).",
                 f"Electrical problem in ignition coil {letter} (usually associated with cylinder {k + 1}).")[i],
                ("Se a luz piscar, pare: falha de ignição pode danificar o catalisador.",
                 "If the light flashes, stop: a misfire can damage the catalytic converter.")[i],
                (["Bobina de ignição", "Conector da bobina", "Vela do cilindro", "Fiação"],
                 ["Ignition coil", "Coil connector", "Spark plug of the cylinder", "Wiring"])[i],
                (["Troque a bobina de lugar com outra e veja se o código muda", "Troque as velas", "Verifique o conector"],
                 ["Swap the coil with another one and see whether the code moves", "Replace the spark plugs", "Check the connector"])[i]))
        if re.fullmatch(r"P07(5[0-9]|6[0-9]|7[0-3])", code):
            n = int(code[1:])
            descs = (("com mau funcionamento", "desempenho ruim ou preso desligado", "preso ligado", "falha elétrica"),
                     ("malfunction", "poor performance or stuck off", "stuck on", "electrical fault"))
            for start, letter in ((750, "A"), (755, "B"), (760, "C"), (765, "D"), (770, "E")):
                if start <= n <= start + 3:
                    j = n - start
                    return _bi(lambda i: _entry(
                        (f"Solenoide de troca de marchas {letter}: {descs[0][j]}", f"Shift solenoid {letter}: {descs[1][j]}")[i],
                        ("Câmbio", "Transmission")[i], 3,
                        (f"O solenoide {letter} do câmbio automático apresenta {descs[0][j]}.",
                         f"Automatic transmission solenoid {letter} reports: {descs[1][j]}.")[i],
                        "", (["Óleo do câmbio (ATF) baixo ou degradado", "Solenoide com defeito", "Fiação ou conector do câmbio"],
                             ["Low or degraded transmission fluid (ATF)", "Faulty solenoid", "Transmission wiring or connector"])[i],
                        (["Confira nível e estado do óleo do câmbio", "Peça diagnóstico em oficina de câmbio automático"],
                         ["Check the level and condition of the transmission fluid", "Have it diagnosed at an automatic-transmission workshop"])[i]))
        return None

    # --------------------------------------------------------------- scope
    @staticmethod
    def is_generic(code: str) -> bool:
        d1 = code[1]
        return d1 in "02" or (d1 == "3" and code[2] in "456789")

    def search_url(self, code: str, lang: str) -> str:
        q = SEARCH_Q[lang].format(car=self.car_label or "car", code=code).strip()
        return "https://www.google.com/search?q=" + urllib.parse.quote(q)

    # -------------------------------------------------------------- lookup
    def lookup(self, code: str) -> dict:
        code = code.upper()
        source = "curated"
        entries = self._curated(code)
        if entries is None:
            entries = self._family(code)
            source = "family"
        sae = self._sae_titles().get(code)
        fam = FAMILY_GENERIC.get(code[:3]) or FAMILY_GENERIC.get(code[:2])
        if entries is None and sae:
            source = "sae"
            entries = {lang: _entry(sae, (fam or AREA.get(code[0], B("", "")))[lang], 2, WHAT_SAE[lang], "", [],
                                    ACTIONS_GENERIC[lang]) for lang in LANGS}
        if entries is None:
            source = "generic"
            entries = {lang: _entry(GENERIC_TITLE[lang], (fam or AREA.get(code[0], B("", "")))[lang], 2,
                                    GENERIC_WHAT[lang].format(fam=(GENERIC_FAM[lang].format(fam=fam[lang]) if fam else "")).strip(),
                                    "", [], ACTIONS_GENERIC[lang]) for lang in LANGS}
        sev = int(entries["en"]["severity"])
        generic = self.is_generic(code)
        t = {}
        for lang in LANGS:
            e = entries[lang]
            t[lang] = {
                "title": e["title"],
                "system": e["system"],
                "severity_text": SEVERITY_TEXT[sev][lang],
                "can_drive": e["r"] or DRIVE_DEFAULT[sev][lang],
                "what": e["e"] or WHAT_DEFAULT[lang].format(title=e["title"]),
                "causes": list(e["c"]),
                "actions": list(e["a"]),
                "brand_tip": e["h"] if self.honda else "",
                "scope": SCOPE[generic][lang],
                "area_text": AREA.get(code[0], B("", ""))[lang],
                "note": NOTE_SAE[lang] if source == "sae" else "",
                "search_url": self.search_url(code, lang),
            }
        return {"code": code, "severity": sev, "source": source, "generic": generic, "area": code[0],
                "brand": "Honda" if self.honda else "", "t": t}

    def tips(self, codes: List[str]) -> List[Msg]:
        codes = [c.upper() for c in codes]
        return [Msg(msg) for cond, msg in _COMBOS if codes and cond(codes)]
