// Application-specific drawing layered on Berries' objective map renderer.
const MAP_MARGIN = 100;

export function formatTime(milliseconds) {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds % 3600 / 60);
  const seconds = totalSeconds % 60;
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export class MapOverlays {
  constructor(renderer, viewport, geometry) {
    this.renderer = renderer;
    this.viewport = viewport;
    this.geometry = geometry;
  }

  drawEntity(entity, room, excludedEntities) {
    const renderer = this.renderer;
    const ctx = renderer.ctx;
    const scale = this.viewport.scale;
    const x = room.x + entity.x;
    const y = room.y + entity.y;
    const excluded = entity.key && excludedEntities.has(entity.key);
    if (excluded) ctx.globalAlpha = .25;
    if (entity.kind === 'audit') {
      ctx.strokeStyle = '#f25b9a';
      ctx.lineWidth = 2 / scale;
      ctx.strokeRect(x - 4 / scale, y - 4 / scale, 8 / scale, 8 / scale);
      if (excluded) ctx.globalAlpha = 1;
      return;
    }
    const legacyName = entity.kind === 'strawberry' || entity.kind === 'moonberry' || entity.kind === 'cassette'
      ? entity.kind : entity.kind.includes('goldenberry') ? 'goldenberry' : entity.kind.includes('heart') ? 'heart' : undefined;
    if (!renderer.drawSprite(entity.sprite ?? legacyName, x, y)) {
      ctx.strokeStyle = '#f25b9a';
      ctx.lineWidth = 2 / scale;
      ctx.strokeRect(x - 4, y - 4, 8, 8);
    }
    if (excluded) ctx.globalAlpha = 1;
  }

  drawEntrance(entrance, mode) {
    const ctx = this.renderer.ctx;
    const scale = this.viewport.scale;
    const box = this.geometry.entranceBox(entrance);
    if (!box) return;
    const x = box.x + box.width / 2;
    const color = entrance.available ? '#ffad42' : '#775b37';
    if (box.width || box.height) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeRect(box.x, box.y, box.width, box.height);
    }
    if (mode !== 'preview') return;
    ctx.font = `${12 / scale}px sans-serif`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    const label = entrance.targetTitle;
    const metrics = ctx.measureText(label);
    const labelX = x - metrics.width / 2;
    const labelY = box.y - 9 / scale;
    this.drawOutlinedText(label, labelX, labelY, color);
  }

  drawOutlinedText(text, x, y, color) {
    const renderer = this.renderer;
    const ctx = renderer.ctx;
    const scale = this.viewport.scale;
    ctx.lineWidth = 3 / scale;
    ctx.strokeStyle = '#000';
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  draw({ state, mode, edit, highlightedRoom, selectionBox, showRoute, showTime, timeMode }) {
    const renderer = this.renderer;
    const ctx = renderer.ctx;
    const scale = this.viewport.scale;
    if (!state) return;
    const selected = edit.selected;
    const roomByName = this.geometry.roomByName;
    this.viewport.beginFrame(ctx);
    ctx.imageSmoothingEnabled = false;
    const box = renderer.bounds(state.rooms, MAP_MARGIN);
    ctx.fillStyle = '#101010';
    ctx.fillRect(box.minX, box.minY, box.maxX - box.minX, box.maxY - box.minY);
    for (const room of box.rooms) {
      ctx.fillStyle = '#080808';
      ctx.fillRect(room.x, room.y, room.width, room.height);
      renderer.tileRows(room, room.background, '#303941');
      renderer.tileRows(room, room.solids, '#bdc6cd');
      if (!room.respawns.length) {
        ctx.fillStyle = 'rgba(0, 0, 0, .48)';
        ctx.fillRect(room.x, room.y, room.width, room.height);
      }
    }
    const order = new Map();
    if (showRoute) {
      state.firstClearRooms.forEach((name, index) => { if (!order.has(name)) order.set(name, index + 1); });
      ctx.strokeStyle = '#58d8ef';
      ctx.lineWidth = 2 / scale;
      ctx.setLineDash([5 / scale, 4 / scale]);
      ctx.beginPath();
      let started = false;
      for (const name of state.firstClearRooms) {
        const room = roomByName.get(name);
        if (!room) continue;
        const x = room.x + room.width / 2;
        const y = room.y + room.height / 2;
        if (started) ctx.lineTo(x, y);
        else { ctx.moveTo(x, y); started = true; }
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const room of box.rooms) {
      const highlighted = highlightedRoom === room.name;
      ctx.strokeStyle = highlighted ? '#f06464' : selected.has(room.name) ? '#75ee47' : '#65717d';
      ctx.lineWidth = (highlighted || selected.has(room.name) ? 3 : 1) / scale;
      ctx.strokeRect(room.x, room.y, room.width, room.height);
      for (const entity of room.entities) this.drawEntity(entity, room, edit.excludedEntities);
      for (const item of room.respawns) renderer.respawn(item, room);
      for (const entrance of state.entrances) if (entrance.room === room.name) this.drawEntrance(entrance, mode);
      const index = order.get(room.name);
      if (index) {
        ctx.font = `${15 / scale}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        this.drawOutlinedText(String(index), room.x + room.width / 2, room.y + room.height / 2, '#58d8ef');
      }
      const firstClearTime = timeMode === 'cumulative'
        ? room.firstClearCumulativeTime
        : room.firstClearTime;
      if (showTime && Number.isFinite(firstClearTime)) {
        ctx.font = `${12 / scale}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const y = room.y + room.height / 2 + (index ? 14 : 0) / scale;
        this.drawOutlinedText(formatTime(firstClearTime), room.x + room.width / 2, y, '#d2d9de');
      }
    }
    if (selectionBox) {
      ctx.strokeStyle = '#54c6ff';
      ctx.lineWidth = 1.5 / scale;
      ctx.setLineDash([4 / scale, 3 / scale]);
      ctx.strokeRect(selectionBox.x, selectionBox.y, selectionBox.width, selectionBox.height);
      ctx.setLineDash([]);
    }
  }
}
