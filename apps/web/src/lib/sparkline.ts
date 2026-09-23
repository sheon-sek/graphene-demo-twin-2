/** An SVG path through `values` scaled into a `width` × `height` box; NaN breaks the line. */
export function sparklinePath(values: number[], width: number, height: number): string {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return '';
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const y = (v: number) => (max === min ? height / 2 : height - ((v - min) / (max - min)) * height);
  let path = '';
  let pen = false;
  values.forEach((v, i) => {
    if (!Number.isFinite(v)) {
      pen = false;
      return;
    }
    path += `${pen ? 'L' : 'M'}${round(i * step)},${round(y(v))}`;
    pen = true;
  });
  return path;
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
