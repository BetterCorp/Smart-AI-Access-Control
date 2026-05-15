export type Box = { x: number; y: number; width: number; height: number; label: string };

export function drawBoxes(canvas: HTMLCanvasElement, boxes: Box[]): void {
  const context = canvas.getContext("2d");
  if (!context) return;
  context.strokeStyle = "#f0b429";
  context.fillStyle = "#f0b429";
  context.lineWidth = 2;
  boxes.forEach((box) => {
    context.strokeRect(box.x, box.y, box.width, box.height);
    context.fillText(box.label, box.x, Math.max(12, box.y - 4));
  });
}

