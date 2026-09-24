import { BoxGeometry, BufferGeometry, CylinderGeometry, ConeGeometry } from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

/** Procedural low-poly silhouettes, one family per kind of equipment (never per instance). */
export const FAMILIES = [
  'chiller',
  'tower',
  'pump',
  'valve',
  'tank',
  'airHandler',
  'ceilingUnit',
  'coolingBlock',
  'ups',
  'panel',
  'meter',
  'breaker',
  'genset',
  'fuelTank',
  'sensor',
  'leakSensor',
  'network',
  'rack',
  'itRow',
  'weatherMast',
  'generic',
] as const;
export type Family = (typeof FAMILIES)[number];

/** Ordered: the first matching rule wins. */
const RULES: [RegExp, Family][] = [
  [/^Chiller$/, 'chiller'],
  [/^Cooling Tower$/, 'tower'],
  [/Pump|Pump header view/, 'pump'],
  [/Valve/, 'valve'],
  [/^Diesel$/, 'fuelTank'],
  [/Tank/, 'tank'],
  [/^(CRAC|PAHU|FWU|FCU)$/, 'airHandler'],
  [/^Ceiling Cooling Units$/, 'ceilingUnit'],
  [/^Cooling Block$/, 'coolingBlock'],
  [/^UPS$/, 'ups'],
  [/^(IPS|RCMS|Chiller Plant Controller)$/, 'panel'],
  [/^Breaker$/, 'breaker'],
  [/^Genset$/, 'genset'],
  [
    /^(Temperature and Humidity|Environment Monitoring|Smoke Detector|Heat Detector|Manual Call Point)$/,
    'sensor',
  ],
  [/^(Fire Zone|Lift)$/, 'panel'],
  [/^Water Leak Cable Sensor$/, 'leakSensor'],
  [/^Network (Device|Switch)$/, 'network'],
  [/^(CDU|Production\/.*)$/, 'rack'],
  [/^IT Load$/, 'itRow'],
  [/^Weather Station$/, 'weatherMast'],
  [/^(BCPM|GPM96|GPQM96|GPQM144|GEM\d+.*)$/, 'meter'],
];

export function familyOf(typeId: string): Family {
  return RULES.find(([pattern]) => pattern.test(typeId))?.[1] ?? 'generic';
}

type Part = BufferGeometry;

function box(w: number, h: number, d: number, x = 0, y = 0, z = 0): Part {
  return new BoxGeometry(w, h, d).translate(x, y + h / 2, z);
}

function cylinder(r: number, h: number, x = 0, y = 0, z = 0, segments = 10): Part {
  return new CylinderGeometry(r, r, h, segments).translate(x, y + h / 2, z);
}

function lying(r: number, length: number, x = 0, y = 0, z = 0, segments = 10): Part {
  // A cylinder on its side along x, resting on y.
  return new CylinderGeometry(r, r, length, segments).rotateZ(Math.PI / 2).translate(x, y + r, z);
}

/** The parts of a family's silhouette; the first is its body, which the camera and clicks aim at. */
function build(family: Family): Part[] {
  switch (family) {
    case 'chiller': // shell-and-tube barrels on a skid, compressor on top
      return [
        lying(0.55, 5.4, 0, 0.3, -0.55),
        box(6, 0.3, 2.4),
        lying(0.55, 5.4, 0, 0.3, 0.55),
        box(1.6, 0.9, 1.2, 0.6, 1.4),
      ];
    case 'tower': // cell casing, fan stack
      return [box(3.6, 3.2, 3.6), cylinder(1.4, 0.8, 0, 3.2, 0, 12)];
    case 'pump':
      return [lying(0.3, 0.7, -0.3, 0.2), box(1.4, 0.2, 0.7), cylinder(0.32, 0.55, 0.4, 0.2)];
    case 'valve':
      return [
        new ConeGeometry(0.28, 0.45, 6).rotateZ(Math.PI / 2).translate(-0.22, 0.4, 0),
        new ConeGeometry(0.28, 0.45, 6).rotateZ(-Math.PI / 2).translate(0.22, 0.4, 0),
        cylinder(0.06, 0.45, 0, 0.4),
        box(0.35, 0.1, 0.35, 0, 0.85),
        box(0.16, 0.2, 0.16),
      ];
    case 'tank':
      return [cylinder(1.4, 3.2, 0, 0, 0, 14)];
    case 'fuelTank':
      return [lying(1.1, 5, 0, 0, 0, 12)];
    case 'airHandler':
      return [box(2.6, 2.0, 1.2), box(2.2, 0.08, 1.24, 0, 1.3)];
    case 'ceilingUnit':
      return [box(8, 0.5, 1.2, 0, 3.3), cylinder(0.04, 3.3, -3.6), cylinder(0.04, 3.3, 3.6)];
    case 'coolingBlock': // manifold with branch stubs
      return [lying(0.25, 3, 0, 0.3), cylinder(0.12, 0.8, -1, 0), cylinder(0.12, 0.8, 1, 0)];
    case 'ups':
      return [box(2.4, 2.0, 0.9), box(2.4, 0.15, 0.95, 0, 1.6)];
    case 'panel':
      return [box(1.2, 2.0, 0.5)];
    case 'meter':
      return [box(0.5, 1.5, 0.3), box(0.36, 0.3, 0.04, 0, 1.05, 0.16)];
    case 'breaker':
      return [box(0.4, 1.3, 0.3), box(0.12, 0.2, 0.06, 0, 0.8, 0.17)];
    case 'genset':
      return [box(5, 2.2, 1.8, 0, 0.3), box(5.5, 0.3, 2), cylinder(0.18, 0.9, 1.8, 2.5)];
    case 'sensor':
      return [box(0.3, 0.3, 0.3, 0, 1.8), cylinder(0.08, 1.8)];
    case 'leakSensor':
      return [box(1.6, 0.06, 0.12)];
    case 'network':
      return [box(0.6, 1.2, 0.6), box(0.62, 0.12, 0.62, 0, 0.9)];
    case 'rack':
      return [box(0.8, 2.1, 1.1)];
    case 'itRow': // a row of IT racks
      return [box(4.8, 2.1, 1.1), box(4.8, 0.1, 1.2, 0, 2.1)];
    case 'weatherMast': // instrument box on a mast, wind vane on top
      return [box(0.5, 0.5, 0.4, 0, 1.6), cylinder(0.05, 2.6), box(0.9, 0.06, 0.06, 0, 2.6)];
    case 'generic':
      return [box(0.8, 0.8, 0.8)];
  }
}

interface Silhouette {
  geometry: BufferGeometry;
  focus: number;
}

const cache = new Map<Family, Silhouette>();

function silhouette(family: Family): Silhouette {
  let found = cache.get(family);
  if (!found) {
    const parts = build(family);
    parts[0].computeBoundingBox();
    const body = parts[0].boundingBox!;
    const geometry = mergeGeometries(parts.map((g) => g.toNonIndexed()), false)!;
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    found = { geometry, focus: (body.min.y + body.max.y) / 2 };
    cache.set(family, found);
  }
  return found;
}

/** One merged, flat-shaded geometry per family, standing on y = 0, centred on x and z. */
export function familyGeometry(family: Family): BufferGeometry {
  return silhouette(family).geometry;
}

/** Height above the floor of the family's body: where the camera looks and a click lands. */
export function familyFocus(family: Family): number {
  return silhouette(family).focus;
}
