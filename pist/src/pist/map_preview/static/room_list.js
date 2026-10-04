// Room-list controls and first-clear labels; editing and camera changes belong to the page.
import { formatTime } from './overlays.js';

function firstClearData(room) {
  if (!Number.isFinite(room.firstClearTime) && !Number.isFinite(room.firstClearDeath)) return;
  return {
    time: Number.isFinite(room.firstClearTime) ? formatTime(room.firstClearTime) : '',
    death: Number.isFinite(room.firstClearDeath) ? String(room.firstClearDeath) : '',
  };
}

export class RoomList {
  constructor(node, filter, { onToggle, onCount, onSelect }) {
    this.node = node;
    this.filter = filter;
    this.onToggle = onToggle;
    this.onCount = onCount;
    this.onSelect = onSelect;
  }

  render({ rooms, mode, edit, canvasSelectedRoom, flashingRoom, reveal }) {
    const roomsNode = this.node;
    const filter = this.filter;
    const query = filter.value.trim().toLocaleLowerCase();
    roomsNode.replaceChildren();
    for (const room of rooms) {
      if (query && !room.name.toLocaleLowerCase().includes(query)) continue;
      const row = document.createElement('div');
      row.className = 'room';
      row.classList.toggle('canvas-selected', room.name === canvasSelectedRoom);
      row.classList.toggle('flash', room.name === flashingRoom);
      const text = document.createElement('span');
      text.className = 'room-name';
      text.textContent = room.name || '(未命名房间)';
      if (mode === 'edit_route') {
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = edit.selected.has(room.name);
        input.addEventListener('click', event => event.stopPropagation());
        input.addEventListener('dblclick', event => event.stopPropagation());
        input.addEventListener('change', () => {
          this.onToggle(room, input.checked);
        });
        row.append(input);
      }
      const content = document.createElement('div');
      content.className = 'room-content';
      content.append(text);
      if (mode === 'edit_route') {
        const adjustment = document.createElement('div');
        adjustment.className = 'room-adjustment';
        const respawns = document.createElement('span');
        respawns.className = 'respawn-count';
        respawns.textContent = `检测到 ${room.respawnCount} 个存档点`;
        const label = document.createElement('label');
        label.textContent = '实际房间';
        const decrement = document.createElement('button');
        decrement.type = 'button';
        decrement.className = 'room-count-button';
        decrement.textContent = '−';
        const input = document.createElement('input');
        input.type = 'text';
        input.inputMode = 'numeric';
        input.pattern = '[0-9]*';
        input.value = String(edit.roomCounts.get(room.name) ?? 1);
        input.className = 'room-count-input';
        const increment = document.createElement('button');
        increment.type = 'button';
        increment.className = 'room-count-button';
        increment.textContent = '+';
        const stop = event => event.stopPropagation();
        for (const control of [decrement, input, increment]) {
          control.addEventListener('click', stop);
          control.addEventListener('dblclick', stop);
        }
        decrement.addEventListener('click', () => this.onCount(room, (edit.roomCounts.get(room.name) ?? 1) - 1));
        increment.addEventListener('click', () => this.onCount(room, (edit.roomCounts.get(room.name) ?? 1) + 1));
        input.addEventListener('change', () => this.onCount(room, Number(input.value)));
        label.append(decrement, input, increment);
        adjustment.append(respawns, label);
        content.append(adjustment);
      }
      const firstClear = firstClearData(room);
      if (firstClear) {
        const data = document.createElement('span');
        data.className = 'room-data';
        const time = document.createElement('span');
        time.textContent = firstClear.time;
        const death = document.createElement('span');
        death.textContent = firstClear.death;
        data.append(time, death);
        content.append(data);
      }
      row.append(content);
      row.addEventListener('click', () => this.onSelect(room, false));
      row.addEventListener('dblclick', () => this.onSelect(room, true));
      roomsNode.append(row);
      if (room.name === reveal) {
        roomsNode.scrollTop = Math.max(0, row.offsetTop - (roomsNode.clientHeight - row.offsetHeight) / 2);
      }
    }
  }
}
