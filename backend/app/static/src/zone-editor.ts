export type Point = { x: number; y: number };

export function normalizePolygon(points: Point[], width: number, height: number): Point[] {
  return points.map((point) => ({
    x: width > 0 ? point.x / width : 0,
    y: height > 0 ? point.y / height : 0,
  }));
}

