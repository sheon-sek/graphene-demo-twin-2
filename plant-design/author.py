"""Authored Plant Design for the Graphene Demo Twin (ADR-0002).

Hand-written source of the site's physical design: rooms, asset placement and
physical connections. It reads the frozen Asset Model export only to check
that every exported asset is placed. Run from the repo root:

    python plant-design/author.py plant-design/plant-design.json
"""
import json, re, collections, sys
from pathlib import Path
REF = Path(__file__).resolve().parent.parent / 'reference/graphene/real-graphene-demo-tag-instances.json'
d = json.load(open(REF))

# ---- Asset inventory straight from the export: root UDT instances (not nested in another UDT)
assets = []
def walk(n, path, in_udt):
    for c in n.get('tags', []):
        p = f"{path}/{c['name']}" if path else c['name']
        if c.get('tagType') == 'UdtInstance':
            if not in_udt:
                assets.append({'path': p, 'type': c.get('typeId'), 'name': c['name']})
            walk(c, p, True)
        elif c.get('tagType') == 'Folder':
            walk(c, p, in_udt)
walk(d, '', False)
A = {a['path']: a for a in assets}
assert len(assets) == 639, len(assets)

def nat(s): return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]

# ---- Spaces: floor -> rooms with plan rectangles (metres, building 96 x 60)
FLOORS = ['Ground', 'Level 1', 'Level 2', 'Roof']
rooms = {}
def room(rid, floor, name, x, y, w, h, kind, fire=None, outdoor=False):
    rooms[rid] = dict(id=rid, floor=floor, name=name, x=x, y=y, w=w, h=h, kind=kind, fire=fire, outdoor=outdoor)
# Ground
room('G-HV', 'Ground', 'HV intake & transformers', 0, 0, 22, 22, 'electrical', 'Zone 1')
room('G-UPSA', 'Ground', 'UPS room A', 22, 0, 26, 24, 'electrical', 'Zone 2')
room('G-UPSB', 'Ground', 'UPS room B', 48, 0, 26, 24, 'electrical', 'Zone 3')
room('G-BAT', 'Ground', 'Battery rooms A/B', 74, 0, 22, 24, 'electrical', 'Zone 3')
room('G-WTR', 'Ground', 'Water plant room', 0, 22, 22, 38, 'water', 'Zone 4')
room('G-CORE', 'Ground', 'Lift core & lobby', 44, 26, 10, 10, 'core', None)
room('G-NOC', 'Ground', 'Security & loading', 54, 36, 42, 24, 'support', 'Zone 5')
room('G-AIR', 'Ground', 'Ground AHU / CRAC plant', 22, 36, 32, 24, 'airside', 'Zone 5')
room('G-GEN', 'Ground', 'Genset yard (outdoor)', 102, 0, 34, 40, 'electrical', 'Zone 1', outdoor=True)
room('G-DSL', 'Ground', 'Diesel tank farm (outdoor)', 102, 44, 34, 16, 'fuel', 'Zone 1', outdoor=True)
# Level 1 / Level 2 — four Data Halls each over the same grid
for lvl, halls, sup, com in (('Level 1', ['DH01', 'DH02', 'DH03', 'DH04'], 'L1 support: DB room & BMS control room', 'L1 common area'),
                             ('Level 2', ['DH05', 'DH06', 'DH07', 'DH08'], 'L2 support: MSB A & MSB B rooms', 'L2 common area')):
    k = 'L1' if lvl == 'Level 1' else 'L2'
    for i, h in enumerate(halls):
        room(h, lvl, f'Data Hall {h}', i * 24, 0, 24, 30, 'hall', f'Zone {i + 1}')
    room(f'{k}-SUP', lvl, sup, 0, 36, 44, 24, 'support', 'Support Area')
    room(f'{k}-COM', lvl, com, 54, 36, 42, 24, 'support', 'Common Area')
    room(f'{k}-CORE', lvl, 'Lift core', 44, 36, 10, 24, 'core', None)
    room(f'{k}-COR', lvl, 'Corridor', 0, 30, 96, 6, 'corridor', 'Common Area')
# Roof
room('R-CT1', 'Roof', 'Tower plant P1 (cells 1–10)', 0, 0, 48, 14, 'cooling', 'Zone 2', outdoor=True)
room('R-CT2', 'Roof', 'Tower plant P2 (cells 1–10)', 48, 0, 48, 14, 'cooling', 'Zone 3', outdoor=True)
room('R-CHP', 'Roof', 'Chiller plant room', 0, 18, 40, 42, 'cooling', 'Zone 1')
room('R-BT', 'Roof', 'Buffer tank bay', 40, 18, 22, 26, 'cooling', 'Zone 4')
room('R-AIR', 'Roof', 'Roof AHU / CRAC / FWU deck', 62, 18, 34, 26, 'airside', 'Support Area', outdoor=True)
room('R-WT', 'Roof', 'Roof water tanks & boosters', 54, 44, 42, 16, 'water', 'Zone 4')
room('R-CORE', 'Roof', 'Lift motor room', 44, 46, 10, 10, 'core', 'Support Area')

# ---- Placement + connections
place = {}      # path -> dict(room, x, y, role, sys)
edges = []      # dict(kind, a, b, label)
def put(path, rid, x, y, role, sysname):
    assert path in A or path.startswith('~'), path
    place[path] = dict(room=rid, x=round(x, 1), y=round(y, 1), role=role, sys=sysname)
def link(kind, a, b, label=''):
    edges.append(dict(kind=kind, a=a, b=b, label=label))
unexported = {}
def ghost(pid, name, typ, observed_by):
    unexported[pid] = dict(id=pid, name=name, type=typ, observedBy=observed_by)
    A[pid] = {'path': pid, 'type': typ, 'name': name, 'unexported': True}

# Cooling plant — 4 chillers; CH-004 unexported
CH = {1: 'Chiller/R_C1', 2: 'Chiller/R_C2', 3: 'Chiller/R_C3', 4: '~CH-004'}
ghost('~CH-004', 'CH-004', 'Chiller', ['Chiller System Control/Chillers/CH-004', 'Chiller_System/Chillers/CH-004'])
for n in range(1, 5):
    put(CH[n], 'R-CHP', 4 + (n - 1) * 9, 24, f'Water-cooled centrifugal chiller CH-00{n}', 'Cooling')
    pch = f'Chiller/R_CP{n}'; pcw = f'Chiller/R_CP{n + 4}'
    put(pch, 'R-CHP', 4 + (n - 1) * 9, 36, f'Primary CHW pump for CH-00{n} (Chiller_System …/CHWS-00{n})', 'Cooling')
    put(pcw, 'R-CHP', 7 + (n - 1) * 9, 36, f'Condenser-water pump for CH-00{n} (Chiller_System …/CHWR-00{n})', 'Cooling')
    link('chw', pch, CH[n], 'primary CHW'); link('cw', pcw, CH[n], 'condenser water')
    ev = f'Chiller/R_CV{n}'; cd = f'Chiller/R_CV{n + 4}'
    put(ev, 'R-CHP', 4 + (n - 1) * 9, 30, f'Evaporator isolation valve CH-00{n} (…/MV-01)', 'Cooling')
    put(cd, 'R-CHP', 7 + (n - 1) * 9, 30, f'Condenser isolation valve CH-00{n} (…/MV-02)', 'Cooling')
    link('chw', ev, CH[n]); link('cw', cd, CH[n])
    hv = f'Chiller/R_CV{n + 8}'; bv = f'Chiller/R_CV{n + 12}'
    put(hv, 'R-CHP', 4 + (n - 1) * 9, 44, f'Header motorized valve MV-00{n}', 'Cooling')
    put(bv, 'R-CHP', 7 + (n - 1) * 9, 44, f'Supply/return bypass valve BV-00{n}', 'Cooling')
    link('chw', CH[n], hv, 'to CHW header')
    for k in (2 * n - 1, 2 * n):
        bt = f'Buffer Tank/R_BT{k}'
        put(bt, 'R-BT', 44 + ((k - 1) % 4) * 5, 22 + ((k - 1) // 4) * 12, f'Buffer tank BT-00{k} on CH-00{n} leg', 'Cooling')
        link('chw', hv, bt, 'buffer')
    # Tower Group CT-00n: five cells
    plant = 1 if n <= 2 else 2
    first = 1 if n % 2 == 1 else 6
    for c in range(first, first + 5):
        ct = f'Cooling Towers Plant/R_P{plant}_CT{c}'
        mp = f'Cooling Towers Plant/R_P{plant}_P{c}'
        x0 = (0 if plant == 1 else 48) + (c - 1) * 4.8 + 2.4
        put(ct, f'R-CT{plant}', x0, 6, f'Tower cell {c} of plant P{plant}; Tower Group CT-00{n} → CH-00{n}', 'Cooling')
        put(mp, f'R-CT{plant}', x0, 12, f'Makeup booster (VSD) for cell P{plant}-{c}', 'Water')
        link('cw', CH[n], ct, 'CW supply'); link('water', mp, ct, 'makeup')
# Standby / secondary distribution pump
put('Chiller/R_CP9', 'R-CHP', 36, 40, 'Secondary CHW distribution pump P-CHWR-01 (DP PID)', 'Cooling')
for n in range(1, 5):
    link('chw', f'Chiller/R_CV{n + 8}', 'Chiller/R_CP9', 'CHW header')

# Cooling Blocks = per-hall CHW branch; unexported branch instruments live in Chiller_System
HALLS = [f'DH0{i}' for i in range(1, 9)]
for i, h in enumerate(HALLS, 1):
    ghost(f'~CB-00{i}', f'CB-00{i}', 'Cooling Block', [f'Chiller_System/Cooling Blocks/CB-00{i}'] + (['Chiller System Control/Cooling Blocks/CB-001'] if i == 1 else []))
    rx = rooms[h]
    put(f'~CB-00{i}', h, rx['x'] + 2, 27, f'CHW branch for {h}', 'Cooling')
    link('chw', 'Chiller/R_CP9', f'~CB-00{i}', 'riser')
    ghost(f'~CCU-00{i}', f'CCU-00{i}', 'Ceiling Cooling Units', [f'Chiller_System/Ceiling Cooling Units/CCU-00{i}'])
    put(f'~CCU-00{i}', h, rx['x'] + 12, 15, f'Ceiling cooling units, {h}', 'Airside')
    link('chw', f'~CB-00{i}', f'~CCU-00{i}')
ghost('~P-CWS-01', 'P-CWS-01', 'Pump header view', ['Chiller System Control/Pumps/P-CWS-01'])
put('~P-CWS-01', 'R-CHP', 36, 34, 'Condenser-water header (aggregate of R_CP5–8)', 'Cooling')

# Airside
def unit_target(prefix, idx):
    if prefix == 'L1': return f'DH0{idx}' if idx <= 4 else 'L1-SUP'
    if prefix == 'R': return f'DH0{idx + 4}' if idx <= 4 else 'L2-SUP'
    return {1: 'G-UPSA', 2: 'G-UPSB', 3: 'G-BAT', 4: 'G-HV', 5: 'G-NOC'}[idx]
for typ, folder, cw in (('CRAC', 'CRAC', False), ('PAHU', 'PAHU', True), ('FWU', 'FWU', True), ('FCU', 'FCU', True)):
    for a in sorted([p for p in A if A[p]['type'] == typ], key=nat):
        nm = A[a]['name']; pre, idx = re.match(r'(G|L1|R)_\D+(\d+)', nm).groups(); idx = int(idx)
        tgt = unit_target(pre, idx)
        if pre == 'R': rid, x, y = 'R-AIR', 64 + (idx - 1) * 6.5, {'CRAC': 22, 'PAHU': 30, 'FWU': 38}[typ]
        elif pre == 'L1':
            r = rooms[tgt]; rid = tgt
            if tgt.startswith('DH'): x = r['x'] + 22.5; y = {'CRAC': 8, 'FCU': 13, 'PAHU': 18}[typ]
            else: x = 42; y = {'CRAC': 39, 'FCU': 43, 'PAHU': 47}[typ]
        else:
            rid = 'G-AIR'; x = 24 + (idx - 1) * 6; y = {'CRAC': 40, 'PAHU': 48, 'FWU': 55}[typ]
        role = {'CRAC': 'DX CRAC (compressor, not on CHW)', 'PAHU': 'Primary AHU — fresh air, dehumidification, pressurisation',
                'FWU': 'Fan-wall unit on CHW', 'FCU': 'Fan-coil unit on CHW'}[typ]
        put(a, rid, x, y, f'{role}; serves {rooms[tgt]["name"]}', 'Airside')
        link('air', a, tgt, 'serves')
        if cw:
            src = f'~CB-00{tgt[3]}' if tgt.startswith('DH') else 'Chiller/R_CP9'
            link('chw', src, a, 'CHW')
# CDUs: DH08 liquid-cooled pod
for i in (1, 2, 3):
    a = f'TIW/CDU-0{i}'
    put(a, 'DH08', 170 - 168 + 72 + 4 + i * 5, 24, 'Coolant distribution unit, DH08 liquid-cooled pod', 'Cooling')
    link('chw', '~CB-008', a, 'facility water'); link('air', a, 'DH08', 'liquid-cooled load')

# Sensors
for a in [p for p in A if A[p]['type'] == 'Environment Monitoring']:
    h = a.split('/')[2]; i = int(a.split(' ')[-1]); r = rooms[h]
    put(a, h, r['x'] + 3 + ((i - 1) % 3) * 9, r['y'] + 4 + ((i - 1) // 3) * 3.6, f'Cold-aisle T/RH, aisle {(i - 1) // 3 + 1} position {(i - 1) % 3 + 1}', 'Environment')
for a in [p for p in A if A[p]['type'] == 'Temperature and Humidity']:
    h = 'DH0' + a.split('/')[1].split(' ')[1]; i = int(a.split(' ')[-1]); j = (i - 1) % 8; r = rooms[h]
    put(a, h, r['x'] + 4.5 + (j % 2) * 9, r['y'] + 5.8 + (j // 2) * 7.2, f'Hot-aisle / return T/RH, hot aisle {j // 2 + 1}', 'Environment')
LEAK_ZONES = {'Ground': {'1A': 'G-WTR', '1B': 'G-WTR', '1C': 'G-WTR', '2A': 'G-UPSA'},
              'Level 1': {'1A': 'DH01', '1B': 'DH02', '2A': 'DH03', '2B': 'DH04', '3A': 'L1-SUP', '3B': 'L1-COM'},
              'Level 2': {'1A': 'DH05', '1B': 'DH06', '2A': 'DH07', '2B': 'DH08', '3A': 'L2-SUP', '3B': 'L2-COM'},
              'Roof': {'1A': 'R-CHP', '1B': 'R-CHP', '2A': 'R-BT', '2B': 'R-BT', '3A': 'R-WT', '3B': 'R-AIR'}}
for a in [p for p in A if A[p]['type'] == 'Water Leak Cable Sensor']:
    fl = a.split('/')[1]; z = a.split('/')[2]; rid = LEAK_ZONES[fl][z]; r = rooms[rid]
    put(a, rid, r['x'] + r['w'] / 2, r['y'] + r['h'] - 2, f'Leak detection cable under {r["name"]}', 'Water')

# Electrical
for i in range(1, 5):
    a = f'Meter/SPPA Incomer {i}'; side = 'A' if i <= 2 else 'B'
    put(a, 'G-HV', 3 + (i - 1) * 5, 5, f'Utility incomer {i} via TX-{i} → MSB {side}', 'Electrical')
    link('power', a, f'Meter/Level 2_MSB {side}_{1 if side == "A" else 9}', f'TX-{i}')
for g in range(1, 7):
    a = f'Genset/Genset {g}'; side = 'A' if g <= 3 else 'B'
    put(a, 'G-GEN', 105 + ((g - 1) % 3) * 10, 8 + ((g - 1) // 3) * 20, f'3,000 kVA genset on genset bus {side} (N+1)', 'Electrical')
    link('power', a, f'Meter/Level 2_MSB {side}_{1 if side == "A" else 9}', f'ATS-{side}')
for t in (1, 2, 3):
    a = f'Diesel/Tank {t}'
    put(a, 'G-DSL', 108 + (t - 1) * 10, 52, f'Bulk diesel tank {t} (50,000 L) with fuel pump & flowmeter', 'Electrical')
    for g in (2 * t - 1, 2 * t): link('fuel', a, f'Genset/Genset {g}', 'fuel')
MSB = {
    'A': {1: ('Main bus meter, MSB A', None), 2: ('UPS feeders A-side, DH01–04', 'ups14'), 3: ('UPS feeders A-side, DH05–08', 'ups58'),
          4: ('Chiller CH-001', CH[1]), 5: ('Chiller CH-002', CH[2]), 6: ('Lighting & small power A (1-phase)', None),
          7: ('MCC-A: Tower Groups CT-001/002, pumps', 'mccA'), 8: ('Airside MCC-A (roof & L1 units)', 'airA')},
    'B': {9: ('Main bus meter, MSB B', None), 10: ('UPS feeders B-side, DH01–04', 'ups14'), 11: ('UPS feeders B-side, DH05–08', 'ups58'),
          12: ('Chiller CH-003', CH[3]), 13: ('Chiller CH-004', CH[4]), 14: ('Lighting & small power B (1-phase)', None),
          15: ('MCC-B: Tower Groups CT-003/004, pumps', 'mccB'), 16: ('Airside MCC-B (ground units)', 'airB')}}
for side, feeders in MSB.items():
    main = f'Meter/Level 2_MSB {side}_{1 if side == "A" else 9}'
    for n, (desc, load) in feeders.items():
        a = f'Meter/Level 2_MSB {side}_{n}'
        k = n - (1 if side == 'A' else 9)
        put(a, 'L2-SUP', (3 if side == 'A' else 24) + (k % 4) * 4.5, 40 + (k // 4) * 8, f'MSB {side} feeder: {desc}', 'Electrical')
        if a != main: link('power', main, a)
        if load and load.startswith(('Chiller', '~')): link('power', a, load)
    link('power', main, 'Meter/Level 1_DB_18', f'ATS-DB ({side})')
# Hall UPS: three per hall — distributed redundant
for h in range(1, 9):
    for k in (1, 2, 3):
        u = 3 * (h - 1) + k
        side = 'A' if k == 1 else 'B' if k == 2 else ('A' if h % 2 else 'B')
        a = f'UPS/UPS {u}'
        room_id = 'G-UPSA' if side == 'A' else 'G-UPSB'
        cnt = collections.Counter(p['room'] for p in place.values())[room_id]
        put(a, room_id, rooms[room_id]['x'] + 3 + (cnt % 5) * 4.8, 4 + (cnt // 5) * 6, f'500 kVA UPS {k} of 3 for DH0{h} (side {side})', 'Electrical')
        feeder = f'Meter/Level 2_MSB {side}_' + (('2' if h <= 4 else '3') if side == 'A' else ('10' if h <= 4 else '11'))
        link('power', feeder, a)
        b = f'BCPM/{h}L{k}'
        r = rooms[f'DH0{h}']
        put(b, f'DH0{h}', r['x'] + 5 + (k - 1) * 7, 28, f'Branch circuit monitor, output of UPS {u} into DH0{h} racks', 'Electrical')
        link('power', a, b); link('power', b, f'DH0{h}', 'IT load')
put('UPS/UPS 25', 'L1-SUP', 41, 57, '100 kVA UPS for BMS control room & network', 'Electrical')
DB = {18: ('L1 DB incomer (from ATS-DB)', None), 19: ('UPS 25 input', 'UPS/UPS 25'), 20: ('IPS panel', None), 21: ('RCMS panel', None),
      22: ('L1 lighting (1-phase)', None), 23: ('Water plant: transfer & booster pumps', None), 24: ('Fire pump & life safety', None)}
for n, (desc, load) in DB.items():
    a = f'Meter/Level 1_DB_{n}'
    put(a, 'L1-SUP', 3 + ((n - 18) % 4) * 4.5, 50 + ((n - 18) // 4) * 5, f'L1 DB: {desc}', 'Electrical')
    if n != 18: link('power', 'Meter/Level 1_DB_18', a)
    if load: link('power', a, load)
for c in range(1, 7):
    put(f'IPS/Circuit {c}', 'L1-SUP', 22 + (c - 1) * 3, 55, f'Isolated power circuit {c} (control room critical)', 'Electrical')
    link('power', 'Meter/Level 1_DB_20', f'IPS/Circuit {c}')
    put(f'RCMS/Circuit {c}', 'L1-SUP', 22 + (c - 1) * 3, 58, f'Residual-current monitored circuit {c}', 'Electrical')
    link('power', 'Meter/Level 1_DB_21', f'RCMS/Circuit {c}')
SUB = {1: 'Tower Group CT-001', 2: 'Tower Group CT-002', 3: 'Tower Group CT-003', 4: 'Tower Group CT-004',
       5: 'Primary CHW pumps R_CP1–4', 6: 'Condenser pumps R_CP5–8', 7: 'Secondary pump R_CP9', 8: 'Makeup pumps plant P1',
       9: 'Makeup pumps plant P2', 10: 'Roof airside units', 11: 'Level 1 airside units', 12: 'Ground airside units',
       13: 'CDU-01–03', 14: 'Lifts 1–3', 15: 'Diesel fuel system', 16: 'Genset auxiliaries', 17: 'BMS & office lighting',
       18: 'Water plant pumps', 19: 'External & security lighting'}
for n, desc in SUB.items():
    a = f'Meter/Meter{n}'
    mcc = 'Meter/Level 2_MSB A_7' if n in (1, 2, 5, 8) else 'Meter/Level 2_MSB B_15' if n in (3, 4, 6, 9) else \
          'Meter/Level 2_MSB A_7' if n == 7 else 'Meter/Level 2_MSB A_8' if n in (10, 11, 13) else 'Meter/Level 2_MSB B_16' if n == 12 else \
          'Meter/Level 1_DB_23' if n == 18 else 'Meter/Level 1_DB_22' if n in (14, 17, 19) else 'Meter/Level 1_DB_24'
    rid = 'L2-SUP' if mcc.startswith('Meter/Level 2') else 'L1-SUP'
    put(a, rid, 4 + (len([p for p in place if place[p]['room'] == rid and p.startswith('Meter/Meter')]) % 10) * 3.8, (51 if rid == 'L2-SUP' else 45) + (len([p for p in place if place[p]['room'] == rid and p.startswith('Meter/Meter')]) // 10) * 3.5, f'Sub-meter: {desc}', 'Electrical')
    link('power', mcc, a)
# Water
put('Cold Water and Sanitary System/G_V1', 'G-WTR', 2, 25, 'Municipal inlet valve', 'Water')
for t in (1, 2):
    put(f'Cold Water and Sanitary System/G_T{t}', 'G-WTR', 5 + (t - 1) * 8, 32, f'Cold-water ground tank {t}', 'Water')
    link('water', 'Cold Water and Sanitary System/G_V1', f'Cold Water and Sanitary System/G_V{t + 1}')
    link('water', f'Cold Water and Sanitary System/G_V{t + 1}', f'Cold Water and Sanitary System/G_T{t}')
    link('water', f'Cold Water and Sanitary System/G_T{t}', f'Cold Water and Sanitary System/G_V{t + 3}')
for v in range(2, 11):
    role = {2: 'Ground tank 1 inlet', 3: 'Ground tank 2 inlet', 4: 'Ground tank 1 outlet', 5: 'Ground tank 2 outlet',
            6: 'Transfer pump 1 discharge', 7: 'Transfer pump 2 discharge', 8: 'Transfer pump 3 discharge', 9: 'Riser isolation', 10: 'AC makeup branch'}[v]
    put(f'Cold Water and Sanitary System/G_V{v}', 'G-WTR', 2 + ((v - 2) % 5) * 4, 40 + ((v - 2) // 5) * 4, role, 'Water')
for p in (1, 2, 3):
    a = f'Cold Water and Sanitary System/G_TP{p}'
    put(a, 'G-WTR', 4 + (p - 1) * 5, 50, f'Transfer pump {p} ({"standby" if p == 3 else "duty"}) → roof tanks', 'Water')
    for t in (4, 5): link('water', f'Cold Water and Sanitary System/G_V{t}', a)
    link('water', a, f'Cold Water and Sanitary System/G_V{p + 5}'); link('water', f'Cold Water and Sanitary System/G_V{p + 5}', 'Cold Water and Sanitary System/G_V9')
for t in (1, 2):
    link('water', 'Cold Water and Sanitary System/G_V9', f'Cold Water and Sanitary System/R_V{t}', 'riser')
    put(f'Cold Water and Sanitary System/R_V{t}', 'R-WT', 57 + (t - 1) * 10, 47, f'Roof tank {t} inlet', 'Water')
    put(f'Cold Water and Sanitary System/R_T{t}', 'R-WT', 60 + (t - 1) * 10, 52, f'Cold-water roof tank {t}', 'Water')
    link('water', f'Cold Water and Sanitary System/R_V{t}', f'Cold Water and Sanitary System/R_T{t}')
    put(f'Cold Water and Sanitary System/R_V{t + 2}', 'R-WT', 63 + (t - 1) * 10, 47, f'Roof tank {t} outlet', 'Water')
    link('water', f'Cold Water and Sanitary System/R_T{t}', f'Cold Water and Sanitary System/R_V{t + 2}')
for b in (1, 2):
    a = f'Cold Water and Sanitary System/R_BP{b}'
    put(a, 'R-WT', 80 + (b - 1) * 5, 52, f'Booster pump {b} → tower makeup header & domestic', 'Water')
    for t in (3, 4): link('water', f'Cold Water and Sanitary System/R_V{t}', a)
for v in range(5, 10):
    role = {5: 'Booster discharge header', 6: 'Makeup header to plant P1', 7: 'Makeup header to plant P2', 8: 'Domestic / sanitary branch', 9: 'Header drain / bypass'}[v]
    put(f'Cold Water and Sanitary System/R_V{v}', 'R-WT', 76 + (v - 5) * 4, 57, role, 'Water')
for b in (1, 2): link('water', f'Cold Water and Sanitary System/R_BP{b}', 'Cold Water and Sanitary System/R_V5')
for pl, v in ((1, 6), (2, 7)):
    link('water', 'Cold Water and Sanitary System/R_V5', f'Cold Water and Sanitary System/R_V{v}')
    for c in range(1, 11): link('water', f'Cold Water and Sanitary System/R_V{v}', f'Cooling Towers Plant/R_P{pl}_P{c}')
link('water', 'Cold Water and Sanitary System/R_V5', 'Cold Water and Sanitary System/R_V8')
for p in (1, 2):
    a = f'AC Makeup Tank/G_P{p}'
    put(a, 'G-WTR', 14 + (p - 1) * 4, 56, f'Closed-loop CHW makeup / pressurisation pump {p}', 'Water')
    link('water', 'Cold Water and Sanitary System/G_V10', a); link('water', a, 'Chiller/R_CP9', 'CHW loop makeup')
link('water', 'Cold Water and Sanitary System/G_V4', 'Cold Water and Sanitary System/G_V10')

# Network — BMS control network in the L1 BMS control room
NET = {'MAIN CORE SWITCH A': None, 'MAIN CORE SWITCH B': None, 'SERVER DISTRIBUTION SWITCH A': 'MAIN CORE SWITCH A',
       'SERVER DISTRIBUTION SWITCH B': 'MAIN CORE SWITCH B', 'GATEWAY A': 'MAIN CORE SWITCH A', 'GATEWAY B': 'MAIN CORE SWITCH B',
       'DATABASE SERVER A': 'SERVER DISTRIBUTION SWITCH A', 'DATABASE SERVER B': 'SERVER DISTRIBUTION SWITCH B',
       'NTP SERVER A': 'SERVER DISTRIBUTION SWITCH A', 'EWS-A': 'SERVER DISTRIBUTION SWITCH A', 'EWS-B': 'SERVER DISTRIBUTION SWITCH B',
       'OWS-A1': 'SERVER DISTRIBUTION SWITCH A', 'OWS-A2': 'SERVER DISTRIBUTION SWITCH A', 'OWS-A3': 'SERVER DISTRIBUTION SWITCH A',
       'OWS-B1': 'SERVER DISTRIBUTION SWITCH B', 'OWS-B2': 'SERVER DISTRIBUTION SWITCH B', 'OWS-B3': 'SERVER DISTRIBUTION SWITCH B',
       'KVM SENDER A': 'SERVER DISTRIBUTION SWITCH A', 'KVM SENDER B': 'SERVER DISTRIBUTION SWITCH B', 'KVM (RECEIVER)': 'MAIN CORE SWITCH A'}
for i, (n, parent) in enumerate(sorted(NET.items(), key=lambda x: nat(x[0]))):
    a = f'Network Topology/{n}'
    put(a, 'L1-SUP', 24 + (i % 5) * 3.5, 42 + (i // 5) * 2.6, 'BMS control-network device (Network Topology view)', 'Network')
    if parent: link('net', f'Network Topology/{parent}', a)
link('net', 'Network Topology/MAIN CORE SWITCH A', 'Network Topology/MAIN CORE SWITCH B', 'stack link')
for s in ('MAIN CORE SWITCH A', 'MAIN CORE SWITCH B', 'SERVER DISTRIBUTION SWITCH A', 'SERVER DISTRIBUTION SWITCH B'):
    a = f'Network Switches/{s}'
    put(a, 'L1-SUP', place[f'Network Topology/{s}']['x'], place[f'Network Topology/{s}']['y'] + 1.2, 'Same physical switch as Network Topology entry — port-level view (48 ports)', 'Network')
GATEWAY_DOMAINS = {'GATEWAY A': ['Cooling', 'Airside', 'Water'], 'GATEWAY B': ['Electrical', 'Environment']}

# Demo Rack — standalone, not on the site
for a in sorted([p for p in A if p.startswith('DemoRack/')], key=nat):
    i = len([p for p in place if p.startswith('DemoRack/')])
    put(a, 'G-NOC', 80 + (i % 6) * 2.4, 50 + (i // 6) * 2.4, 'Demo Rack (standalone cabinet, own incomer; not connected to the site)', 'Demo Rack')
for b in range(1, 15): link('power', 'DemoRack/E820', f'DemoRack/Breaker{b}')

# Support assets — projected only
SUPPORT = ['Smart Alarm Logic/', 'Dashboard/', 'Testing/', 'Meter/decoder1']
for a in A:
    if a.startswith(tuple(SUPPORT)) and a not in place:
        place[a] = dict(room=None, x=None, y=None, role='Support Asset — projected, not simulated', sys='Support')

missing = [a for a in A if a not in place]
assert not missing, missing[:20]

# ---- Plant Views & loose folders
views = [
 dict(folder='Chiller System Control', kind='Plant View (supervisory)', observes='4 chillers, 4 Tower Groups, 8 buffer tanks, CB-001 plant setpoints, CHW/CW header pumps, staging & PID controllers, rotation schedule, DH1–8 temperatures, Weather', points=398),
 dict(folder='Chiller_System', kind='Plant View (instrument)', observes='Per-chiller TS/FM/MV/pump VFD/WCC, CHW header TS/FM/DPS, header valves MV-001–004, bypass BV-001–004, CB-001–008 branches, CCU-001–008', points=124),
 dict(folder='Dashboard', kind='Plant View (KPI)', observes='PUE / WUE / plant efficiency, IT vs facility load, energy by building, floor and Data Hall', points=59),
 dict(folder='Other', kind='Plant View (KPI)', observes='Site IT load, lighting, cooling, power losses, gateway status, maintenance', points=8),
 dict(folder='Fire Protection System', kind='Life-safety devices', observes='SD smoke / HD heat detectors, CP call points, AV alarm valves, FP fire pumps, by floor and fire zone', points=56),
 dict(folder='Lift Monitoring System', kind='Vertical transport', observes='Lifts 1–3 in the central core: level, direction, door', points=12),
 dict(folder='Environment Monitoring/*/DHnn (loose)', kind='Plant View (hall aggregates)', observes='Avg/Max cold-aisle T & RH, IT Load per Data Hall', points=40),
 dict(folder='Breaker, Line, Smart Alarm Logic', kind='Support Asset', observes='Alarm-logic demo (breakers/interlocks/lines) — not the site single-line diagram (confirm)', points=71 + 92 + 246),
 dict(folder='Temperature_Controls, Level_Monitoring, Pressure System, MQTT Tags, PredictionCache, Testing, Device Card Abbreviation', kind='Support Asset', observes='Generic non-datacenter demo tags — bounded deterministic values', points=5 + 5 + 3 + 3 + 5 + 17 + 1),
]
fire = {'Ground': {'Zone 1': 'HV intake, genset yard, diesel farm', 'Zone 2': 'UPS room A', 'Zone 3': 'UPS room B & battery rooms', 'Zone 4': 'Water plant room', 'Zone 5': 'Ground AHU plant, security & loading'},
        'Level 1': {'Zone 1': 'DH01', 'Zone 2': 'DH02', 'Zone 3': 'DH03', 'Zone 4': 'DH04', 'Common Area': 'Corridor, common area', 'Support Area': 'DB room & BMS control room'},
        'Level 2': {'Zone 1': 'DH05', 'Zone 2': 'DH06', 'Zone 3': 'DH07', 'Zone 4': 'DH08', 'Common Area': 'Corridor, common area', 'Support Area': 'MSB A & B rooms'},
        'Roof': {'Zone 1': 'Chiller plant room', 'Zone 2': 'Tower plant P1', 'Zone 3': 'Tower plant P2', 'Zone 4': 'Buffer tank bay & roof water tanks', 'Support Area': 'Roof AHU deck & lift motor room'}}

basis = dict(
  climate='Tropical (Kuala Lumpur / Singapore class): dry bulb 24–33 °C, wet bulb 24–27 °C, RH 60–95 %; daily cycle with a deterministic seasonal drift',
  it=[dict(hall=h, floor='Level 1' if i < 4 else 'Level 2', design_kW=1200 if h == 'DH08' else 1000, operating='55–80 %', note='40 % liquid-cooled via CDU-01–03' if h == 'DH08' else '') for i, h in enumerate(HALLS)],
  capacities=[
    ('Chillers', '4 × 3,500 kWr water-cooled centrifugal, 3 duty + 1 standby, CHW 14 / 20 °C'),
    ('Tower Groups', '4 groups × 5 cells, 1,000 kW rejection per cell at 27 °C WB; CW 32 / 37 °C; VFD fans'),
    ('Buffer tanks', '8 × 30 m³ stratified, 2 per chiller leg'),
    ('Utility', '4 × 5 MVA transformers (SPPA Incomers 1–4), 2 per MSB side'),
    ('Gensets', '6 × 3,000 kVA, 3 per side (N+1), ATS transfer ≤ 15 s'),
    ('Diesel', '3 × 50,000 L bulk tanks, each feeding a genset pair'),
    ('Hall UPS', '24 × 500 kVA, 3 per Data Hall, distributed redundant; ~8 min battery at design load'),
    ('Control UPS', 'UPS 25, 100 kVA, BMS control room & network'),
    ('Water', '2 ground tanks × 200 m³, 2 roof tanks × 60 m³; transfer 2 duty + 1 standby; boosters 1 + 1'),
  ])

reviews = [
 dict(id='A1', area='Cooling', title='Pump roles', text='R_CP1–4 = primary CHW pumps (Chiller_System …/CHWS-00n). R_CP5–8 = condenser-water pumps (…/CHWR-00n; the export calls them CHWR). R_CP9 = secondary distribution pump P-CHWR-01 under the DP PID. P-CWS-01 is read as the aggregate of the condenser pumps.'),
 dict(id='A2', area='Cooling', title='Valve roles', text='R_CV1–4 evaporator isolation (MV-01), R_CV5–8 condenser isolation (MV-02), R_CV9–12 header valves MV-001–004, R_CV13–16 bypass BV-001–004.'),
 dict(id='A3', area='Cooling', title='Buffer tanks sit on chiller legs', text='Two buffer tanks per chiller (BT-00k = R_BTk), because each chiller reports a Buffer Tank Status.'),
 dict(id='A4', area='Airside', title='CRAC units are DX, not chilled water', text='CRAC members include compressors, compressor capacity and high-pressure alarm, and have no CHW temperatures. They keep cooling if the chiller plant fails; high ambient drives head pressure. v1 wired chillers to CRACs, which this corrects.'),
 dict(id='A5', area='Airside', title='Unit 5 on each floor serves support rooms', text='L1_*1–4 serve DH01–04 and R_*1–4 serve DH05–08 (roof-mounted, ducted down the shaft). Unit 5 of each set serves that floor\'s support area. G_*1–5 serve UPS A, UPS B, battery, HV and security rooms.'),
 dict(id='A6', area='Airside', title='Hall cooling mix', text='L1 halls: CRAC (DX) + FCU + CCU (CHW). L2 halls: CRAC (DX) + FWU + CCU (CHW). PAHU provides fresh air and humidity control only. DH08 also has a liquid-cooled pod on CDU-01–03.'),
 dict(id='A7', area='Electrical', title='3 UPS per hall, distributed redundant', text='The site stays 2N at MSB level (A/B). Inside each hall, UPS 3n−2 is on side A, 3n−1 on side B, and 3n alternates sides; BCPM nL1–3 meter the three UPS outputs. Losing one UPS moves the hall onto the other two (each at ≤ 75 %). This refines Q11: strict 2N would need an even UPS count per hall.'),
 dict(id='A8', area='Electrical', title='MSB feeder allocation', text='MSB A_1 / B_9 are the main bus meters. Each side feeds its UPS groups, two chillers, an MCC for its Tower Groups and pumps, an airside MCC, and lighting (the GEM230 single-phase meters).'),
 dict(id='A9', area='Electrical', title='Loose meters Meter1–19', text='Sub-meters on the MCCs, airside boards and L1 DB, listed in the placement table. decoder1 (GEM630-CT-L raw hex/base64) is a Support Asset.'),
 dict(id='A10', area='Environment', title='Two sensor families per hall', text='The 21 Environment Monitoring sensors are cold-aisle sensors (7 aisles × 3). The 8 Temperature and Humidity sensors are hot-aisle / return sensors (4 hot aisles × 2).'),
 dict(id='A11', area='Network', title='Gateways split by domain', text='GATEWAY A carries mechanical field devices (cooling, airside, water); GATEWAY B carries electrical and environment. A comm fault on a gateway turns every point behind it Bad. Network Switches/* are the port-level view of the same four physical switches as Network Topology/*.'),
 dict(id='A12', area='Support', title='Root Breaker/ and Line/ are support', text='These are treated as the Smart Alarm Logic demo, not the site single-line diagram. If they drive an SLD screen in Ignition, say so and they will be bound to the electrical model.'),
 dict(id='A13', area='Site', title='Room layout', text='Ground: HV intake, UPS rooms, water plant, genset yard and diesel farm outdoors. L1: DH01–04, DB room, BMS control room. L2: DH05–08, MSB A/B rooms (the meter names say MSB is on Level 2). Roof: chillers, 20 tower cells, buffer tanks, water tanks, AHU deck. Lifts 1–3 in a central core.'),
 dict(id='A14', area='Water', title='Water path', text='Municipal supply → G_V1 → ground tanks → transfer pumps (2 + 1) → riser → roof tanks → boosters → makeup headers → one VSD makeup pump per tower cell. AC Makeup Tank pumps top up the closed CHW loop from the ground tank branch.'),
]

out = dict(version='0.1', date='2026-09-23', floors=FLOORS, rooms=list(rooms.values()),
           assets=[dict(path=p, type=A[p]['type'], unexported=A[p].get('unexported', False), **place[p]) for p in sorted(A, key=nat)],
           edges=edges, unexported=list(unexported.values()), views=views, fire=fire, leak=LEAK_ZONES,
           gateways=GATEWAY_DOMAINS, basis=basis, reviews=reviews,
           counts=dict(exported=639, unexported=len(unexported), edges=len(edges)))
json.dump(out, open(sys.argv[1], 'w'), ensure_ascii=False, indent=1)
print('ok', len(out['assets']), 'assets', len(edges), 'edges', collections.Counter(e['kind'] for e in edges))
