// Personal route, room counts and exclusions, independent of DOM and HTTP.
export class EditState {
  constructor() {
    this.selected = new Set();
    this.roomCounts = new Map();
    this.excludedEntities = new Set();
  }

  load(state) {
    this.rooms = state.rooms;
    this.readOnly = state.readOnly;
    this.selected = new Set(state.selected);
    this.roomCounts = new Map(state.rooms.map(room => [room.name, room.roomCount ?? 1]));
    this.excludedEntities = new Set(state.rooms.flatMap(room => room.entities.filter(entity => entity.excluded).map(entity => entity.key)));
    this.markSaved();
  }

  editableState() {
    const rooms = this.rooms.filter(room => this.selected.has(room.name));
    return JSON.stringify({
      rooms: rooms.map(room => room.name),
      roomCounts: Object.fromEntries(rooms.map(room => [room.name, this.roomCounts.get(room.name) ?? 1])),
      excludedEntities: [...this.excludedEntities].sort(),
    });
  }

  markSaved() {
    this.savedState = this.editableState();
  }

  get dirty() {
    return this.rooms !== undefined && !this.readOnly && this.editableState() !== this.savedState;
  }

  get roomCount() {
    return [...this.selected].reduce((total, name) => total + (this.roomCounts.get(name) ?? 1), 0);
  }

  setRoomCount(name, count) {
    this.roomCounts.set(name, Number.isInteger(count) ? Math.max(1, count) : 1);
  }

  toggleRoom(name) {
    if (this.selected.has(name)) this.selected.delete(name);
    else this.selected.add(name);
  }

  selectRooms(rooms, { ctrlKey, shiftKey }) {
    for (const room of rooms) {
      if (ctrlKey && shiftKey) this.toggleRoom(room.name);
      else if (ctrlKey) this.selected.delete(room.name);
      else this.selected.add(room.name);
    }
  }

  toggleEntity(key) {
    if (this.excludedEntities.has(key)) this.excludedEntities.delete(key);
    else this.excludedEntities.add(key);
  }

  payload() {
    return { rooms: [...this.selected], roomCounts: Object.fromEntries([...this.selected].map(name => [name, this.roomCounts.get(name) ?? 1])), excludedEntities: [...this.excludedEntities] };
  }

  collectibleSummaryText() {
    const groups = new Map();
    for (const room of this.rooms) {
      for (const entity of room.entities) {
        if (!entity.summary || !entity.key) continue;
        const key = entity.summary.kind;
        let group = groups.get(key);
        if (!group) {
          group = { ...entity.summary, total: 0, active: 0, values: new Set() };
          groups.set(key, group);
        }
        group.total += 1;
        if (!this.excludedEntities.has(entity.key)) {
          group.active += 1;
          if (entity.summary.stat === 'select' && entity.summary.value) {
            group.values.add(entity.summary.value);
          }
        }
      }
    }
    return [...groups.values()].map(group => {
      if (group.stat === 'count') return `${group.label} ${group.active}/${group.total}`;
      if (group.stat === 'exist') return `${group.label} ${group.active ? '有' : '无'}（${group.active}/${group.total}）`;
      const values = [...group.values];
      if (values.length === 0) return `${group.label} 无`;
      if (values.length === 1) return `${group.label} ${values[0]}（${group.active}/${group.total}）`;
      return `${group.label} 冲突（${values.join(' / ')}）`;
    }).join(' · ');
  }
}
