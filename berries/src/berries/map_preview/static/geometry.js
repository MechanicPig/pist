// Spatial queries on objective map data, with no route or personal-statistics semantics.
export class MapGeometry {
  load(rooms, entrances) {
    this.rooms = rooms.filter(room => [room.x, room.y, room.width, room.height].every(Number.isFinite));
    this.roomByName = new Map(this.rooms.map(room => [room.name, room]));
    this.entrances = entrances;
  }

  roomAt(point) {
    return this.rooms.findLast(room => point.x >= room.x && point.x < room.x + room.width
      && point.y >= room.y && point.y < room.y + room.height);
  }

  roomsIntersecting(box) {
    return this.rooms.filter(room => room.x < box.x + box.width && room.x + room.width > box.x
      && room.y < box.y + box.height && room.y + room.height > box.y);
  }

  entranceBox(entrance) {
    const room = this.roomByName.get(entrance.room);
    if (!room || !Number.isFinite(entrance.x) || !Number.isFinite(entrance.y)) return;
    return { x: room.x + entrance.x, y: room.y + entrance.y,
      width: Number.isFinite(entrance.width) ? entrance.width : 0,
      height: Number.isFinite(entrance.height) ? entrance.height : 0 };
  }

  entranceAt(point, { radius = 0, minimumSize = 0, nearest = false } = {}) {
    let best;
    let bestDistance = Infinity;
    for (const entrance of this.entrances) {
      const box = this.entranceBox(entrance);
      if (!box) continue;
      const distance = Math.hypot(point.x - (box.x + box.width / 2), point.y - (box.y + box.height / 2));
      const contains = point.x >= box.x && point.x <= box.x + Math.max(box.width, minimumSize)
        && point.y >= box.y && point.y <= box.y + Math.max(box.height, minimumSize);
      if (!contains && !(radius > 0 && distance <= radius)) continue;
      if (!nearest) return entrance;
      if (distance < bestDistance) { best = entrance; bestDistance = distance; }
    }
    return best;
  }

  entityAt(point, { radius, nearest = false, filter = () => true }) {
    let best;
    let bestDistance = Infinity;
    for (const room of this.rooms) {
      for (const entity of room.entities) {
        if (!filter(entity)) continue;
        const distance = Math.hypot(room.x + entity.x - point.x, room.y + entity.y - point.y);
        if (!(distance <= radius)) continue;
        if (!nearest) return { room, entity };
        if (distance < bestDistance) { best = { room, entity }; bestDistance = distance; }
      }
    }
    return best;
  }
}
