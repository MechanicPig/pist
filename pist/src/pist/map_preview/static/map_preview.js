import { MapCanvas } from './canvas.js';
import { CanvasInteraction, MapViewport } from './viewport.js';
import { MapGeometry } from './geometry.js';
import { PreviewClient } from './client.js';
import { EditState } from './edit_state.js';
import { MapOverlays } from './overlays.js';
import { RoomList } from './room_list.js';
import { ObjectInfoPanel } from './object_info.js';

const base = location.pathname;
const canvas = document.querySelector('#map');
const renderer = new MapCanvas(canvas, base, draw);
const viewport = new MapViewport(canvas);
const geometry = new MapGeometry();
const client = new PreviewClient(base);
const edit = new EditState();
const overlays = new MapOverlays(renderer, viewport, geometry);
const filter = document.querySelector('#filter');
const roomList = new RoomList(document.querySelector('#rooms'), filter, {
  onToggle: (room, checked) => {
    if (checked) edit.selected.add(room.name);
    else edit.selected.delete(room.name);
    canvasSelectedRoom = undefined;
    update();
  },
  onCount: (room, count) => { edit.setRoomCount(room.name, count); update(); },
  onSelect: (room, center) => {
    highlightedRoom = center || highlightedRoom !== room.name ? room.name : undefined;
    if (!center && canvasSelectedRoom === room.name) canvasSelectedRoom = undefined;
    else { canvasSelectedRoom = room.name; selectedRoomToReveal = room.name; }
    update();
    if (center) centerRoom(room);
  },
});
const count = document.querySelector('#count');
const collectibleSummary = document.querySelector('#collectible-summary');
const hint = document.querySelector('#hint');
const objectInfo = new ObjectInfoPanel(document.querySelector('#map-object-info'));
const save = document.querySelector('#save');
const showFirstClearRoute = document.querySelector('#show-first-clear-route');
const showFirstClearTime = document.querySelector('#show-first-clear-time');
const firstClearTimeMode = document.querySelector('#first-clear-time-mode');
let state;
let mode = 'preview';
let highlightedRoom;
let selectionBox;
let finished = false;
let selectedRoomToReveal;
let canvasSelectedRoom;
let flashingRoom;
let flashTimer;
let openingMap = false;

const MAP_MARGIN = 100;
const spriteFiles = {
  strawberry: 'strawberry.png',
  moonberry: 'moonberry.png',
  goldenberry: 'goldenberry.png',
  cassette: 'cassette.png',
  heart: 'heart.png',
};

for (const [name, file] of Object.entries(spriteFiles)) {
  renderer.sprite(name, file);
}

function resize() {
  renderer.resize();
  draw();
}

function setupView() {
  viewport.fit(renderer.bounds(state.rooms, MAP_MARGIN));
}

function draw() {
  overlays.draw({ state, mode, edit, highlightedRoom, selectionBox,
    showRoute: showFirstClearRoute.checked, showTime: showFirstClearTime.checked,
    timeMode: firstClearTimeMode.value });
}

function renderList() {
  roomList.render({ rooms: state.rooms, mode, edit, canvasSelectedRoom, flashingRoom, reveal: selectedRoomToReveal });
  selectedRoomToReveal = undefined;
}

function update() {
  const editingRoute = mode === 'edit_route';
  const readOnly = state.readOnly;
  count.hidden = !editingRoute;
  save.hidden = readOnly;
  document.querySelector('#cancel').hidden = readOnly;
  count.textContent = `已选择 ${edit.selected.size} / ${state.rooms.length}（实际 ${edit.roomCount}）`;
  const summary = edit.collectibleSummaryText();
  collectibleSummary.textContent = summary;
  collectibleSummary.hidden = !summary;
  hint.textContent = editingRoute
    ? '左键切换房间；列表可调整每个房间的实际数量（默认 1，检测到的存档点仅供参考）；从空白处拖框批量选择（Ctrl 减去，Shift+Ctrl 对称差）；中键或右键拖动画布；中键双击复位。'
    : `${readOnly ? '只读预览' : '预览地图'}；左键定位房间；右键查看实体或入口属性并选择操作；中键或右键拖动画布；中键双击复位。`;
  document.querySelector('#back').disabled = !state.canBack;
  document.querySelector('#forward').disabled = !state.canForward;
  document.querySelector('#home').disabled = !state.canHome;
  for (const id of ['back', 'forward', 'home']) {
    document.querySelector(`#${id}`).hidden = readOnly || mode !== 'preview';
  }
  renderList();
  draw();
}

function revealRoom(name) {
  selectedRoomToReveal = name;
  flashingRoom = name;
  if (flashTimer !== undefined) clearTimeout(flashTimer);
  update();
  flashTimer = setTimeout(() => {
    flashingRoom = undefined;
    renderList();
  }, 750);
}

function centerRoom(room) {
  viewport.center(room);
  draw();
}

function selectPreviewRoom(room) {
  highlightedRoom = room.name;
  canvasSelectedRoom = room.name;
  selectedRoomToReveal = room.name;
  update();
}

function showInspectableEntity(item, event) {
  const entity = item.entity;
  objectInfo.show({ id: entity.entityId, source: `${entity.kind} · ${item.room.name}`, attrs: entity.attrs }, event);
  if (!state.readOnly && mode === 'preview' && entity.key && entity.summary) {
    const updateAction = button => {
      button.textContent = edit.excludedEntities.has(entity.key) ? '取消屏蔽' : '屏蔽收集品';
    };
    const action = objectInfo.addAction('', event => {
      edit.toggleEntity(entity.key);
      updateAction(event.currentTarget);
      update();
    });
    updateAction(action);
  }
}

function showEntranceInfo(entrance, event) {
  const source = entrance.source === 'trigger'
    ? 'Trigger'
    : entrance.source === 'entity' ? 'Entity' : '地图入口';
  objectInfo.show({ id: entrance.entityId ?? '地图入口',
    source: `${source} · ${entrance.room} → ${entrance.targetTitle}`, attrs: entrance.attrs }, event);
  if (!state.readOnly && mode === 'preview') {
    objectInfo.addAction(
      entrance.available ? '进入地图' : '目标地图不可用',
      () => {
        objectInfo.hide();
        openMap(entrance.targetSid);
      },
      !entrance.available,
    );
  }
}

function toggleRoom(room) {
  if (mode === 'edit_route') {
    edit.toggleRoom(room.name);
    revealRoom(room.name);
    return;
  }
  if (canvasSelectedRoom === room.name) {
    highlightedRoom = undefined;
    canvasSelectedRoom = undefined;
    update();
    return;
  }
  selectPreviewRoom(room);
}

let selectionStartsInRoom = false;
const interaction = new CanvasInteraction(viewport, {
  redraw: draw,
  reset: () => { if (state) { setupView(); draw(); } },
  zoomFactor: event => event.deltaY < 0 ? 1.15 : 1 / 1.15,
  dragStart: point => { selectionStartsInRoom = state && geometry.roomAt(point) !== undefined; },
  dragMove: (start, point) => {
    if (mode !== 'edit_route' || selectionStartsInRoom || !state) return;
    selectionBox = { x: Math.min(start.x, point.x), y: Math.min(start.y, point.y),
      width: Math.abs(point.x - start.x), height: Math.abs(point.y - start.y) };
  },
  dragEnd: event => {
    if (!selectionBox) return;
    edit.selectRooms(geometry.roomsIntersecting(selectionBox), event);
    selectionBox = undefined;
    update();
  },
  dragCancel: () => { selectionBox = undefined; draw(); },
  click: (point, event) => {
    if (!state) return;
    if (event.button === 2) {
      const item = geometry.entityAt(point, { radius: 12 / viewport.scale, nearest: true, filter: entity => Boolean(entity.entityId) });
      const entrance = geometry.entranceAt(point, { radius: 16 / viewport.scale, nearest: true });
      if (item) showInspectableEntity(item, event);
      else if (entrance) showEntranceInfo(entrance, event);
    } else if (event.button === 0) {
      const room = geometry.roomAt(point);
      if (room) toggleRoom(room);
    }
  },
  doubleClick: (point, event) => {
    if (!state || event.button !== 0 || mode !== 'preview') return;
    const room = geometry.roomAt(point);
    if (room) { selectPreviewRoom(room); centerRoom(room); }
  },
});

function loadState(next, preserveCanvas = false) {
  const initial = state === undefined;
  state = next;
  if (initial) mode = state.initialMode;
  if (state.readOnly) mode = 'preview';
  edit.load(state);
  geometry.load(state.rooms, state.entrances);
  interaction.cancel();
  selectionBox = undefined;
  if (!preserveCanvas) {
    highlightedRoom = undefined;
    canvasSelectedRoom = undefined;
  }
  objectInfo.hide();
  if (highlightedRoom && !geometry.roomByName.has(highlightedRoom)) highlightedRoom = undefined;
  if (canvasSelectedRoom && !geometry.roomByName.has(canvasSelectedRoom)) canvasSelectedRoom = undefined;
  document.title = `地图预览：${state.title}`;
  document.querySelector('#title').textContent = state.title;
  document.querySelector('#mode').hidden = state.readOnly;
  document.querySelector('#overlays').hidden = state.readOnly;
  document.querySelectorAll('input[name="mode"]').forEach(input => {
    input.checked = input.value === mode;
  });
  resize();
  if (!preserveCanvas) setupView();
  update();
}

async function openMap(targetSid) {
  if (openingMap) return;
  if (!await confirmLeavingEdits()) return;
  openingMap = true;
  try {
    loadState(await client.request('open', { targetSid }));
  } catch (error) {
    showError(error.message);
  } finally {
    openingMap = false;
  }
}

async function navigate(action) {
  if (!await confirmLeavingEdits()) return;
  try {
    const data = await client.request(action);
    if (data.closed) { finishPage('已返回，可关闭此页面。'); return; }
    loadState(data);
  } catch (error) { showError(error.message); }
}

function showError(message) {
  const status = document.querySelector('#status');
  status.textContent = message || '操作失败。';
  status.hidden = false;
}

function showStatus(message) {
  document.querySelector('#status').textContent = message;
  document.querySelector('#status').hidden = false;
}

function finishPage(message) {
  finished = true;
  showStatus(message);
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
}

async function saveRoute() {
  try {
    await client.request('save', edit.payload());
    edit.markSaved();
    update();
    showStatus('已保存。');
    return true;
  } catch (error) { showError(error.message); return false; }
}

async function confirmLeavingEdits() {
  if (!edit.dirty) return true;
  if (confirm('当前地图有未保存的修改。是否保存后继续？')) {
    return await saveRoute() === true;
  }
  return confirm('是否放弃未保存的修改并继续？');
}

async function cancel() {
  try {
    loadState(await client.request('cancel'), true);
    showStatus('已还原到上次保存的结果。');
  } catch (error) { showError(error.message); }
}

document.querySelectorAll('input[name="mode"]').forEach(input => {
  input.addEventListener('change', () => {
    if (input.checked) {
      mode = input.value;
      update();
    }
  });
});
showFirstClearRoute.addEventListener('change', draw);
showFirstClearTime.addEventListener('change', () => {
  firstClearTimeMode.disabled = !showFirstClearTime.checked;
  draw();
});
firstClearTimeMode.addEventListener('change', draw);
filter.addEventListener('input', renderList);
document.querySelector('#save').addEventListener('click', saveRoute);
document.querySelector('#cancel').addEventListener('click', cancel);
document.querySelector('#back').addEventListener('click', () => navigate('back'));
document.querySelector('#forward').addEventListener('click', () => navigate('forward'));
document.querySelector('#home').addEventListener('click', () => navigate('home'));
addEventListener('mousedown', event => {
  const action = event.button === 3 ? 'back' : event.button === 4 ? 'forward' : undefined;
  if (action) {
    event.preventDefault();
    event.stopPropagation();
    if (state && (action !== 'back' || state.canBack) && (action !== 'forward' || state.canForward)) {
      navigate(action);
    }
  }
}, { capture: true });
addEventListener('resize', () => {
  resize();
  objectInfo.constrain();
});
new ResizeObserver(resize).observe(canvas);
addEventListener('pagehide', () => {
  interaction.dispose();
  if (!finished) client.leave('abandon');
});
addEventListener('beforeunload', event => {
  if (!finished && edit.dirty) {
    event.preventDefault();
    event.returnValue = '';
  }
});
client.request('state').then(loadState).catch(error => showError(error.message));
