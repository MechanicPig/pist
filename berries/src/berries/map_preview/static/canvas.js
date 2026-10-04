// Objective map rendering shared by the read-only viewer and application overlays.
export class MapCanvas {
  constructor(canvas, base, redraw) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.base = base;
    this.redraw = redraw;
    this.sprites = new Map();
  }

  bounds(rooms, margin = 0) {
    const visible = rooms.filter(room => [room.x, room.y, room.width, room.height].every(Number.isFinite));
    if (!visible.length) return { rooms: [], minX: -100, minY: -100, maxX: 100, maxY: 100 };
    return {
      rooms: visible,
      minX: Math.min(...visible.map(room => room.x)) - margin,
      minY: Math.min(...visible.map(room => room.y)) - margin,
      maxX: Math.max(...visible.map(room => room.x + room.width)) + margin,
      maxY: Math.max(...visible.map(room => room.y + room.height)) + margin,
    };
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.round(rect.width * devicePixelRatio);
    this.canvas.height = Math.round(rect.height * devicePixelRatio);
  }

  tileRows(room, rows, color) {
    this.ctx.fillStyle = color;
    rows.forEach((row, y) => {
      let begin = -1;
      for (let x = 0; x <= row.length; x++) {
        if (x < row.length && row[x] !== '0') {
          if (begin < 0) begin = x;
        } else if (begin >= 0) {
          this.ctx.fillRect(room.x + begin * 8, room.y + y * 8, (x - begin) * 8, 8);
          begin = -1;
        }
      }
    });
  }

  sprite(name, file = name) {
    if (!name) return;
    if (!this.sprites.has(name)) {
      const image = new Image();
      image.src = `${this.base}/game-assets/${file}`;
      image.addEventListener('load', this.redraw);
      this.sprites.set(name, image);
    }
    return this.sprites.get(name);
  }

  drawSprite(name, x, y) {
    const image = this.sprite(name);
    if (!image?.complete || !image.naturalWidth) return false;
    this.ctx.drawImage(image, Math.round(x - image.naturalWidth / 2), Math.round(y - image.naturalHeight));
    return true;
  }

  respawn(item, room) {
    // Align using world coordinates, including rooms whose origin is not tile-aligned.
    const x = room.x + item.x;
    const y = room.y + item.y;
    this.ctx.fillStyle = '#c43d3d';
    this.ctx.fillRect(Math.floor(x / 8) * 8, Math.floor((y - 8) / 8) * 8, 8, 8);
  }
}
