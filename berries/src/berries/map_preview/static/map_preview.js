import { MapCanvas } from './canvas.js';
import { CanvasInteraction, MapViewport } from './viewport.js';
import { MapGeometry } from './geometry.js';
import { PreviewClient } from './client.js';
import { ObjectInfoPanel } from './object_info.js';

const base = location.pathname;
const canvas = document.querySelector('#map');
const renderer = new MapCanvas(canvas, base, draw);
const ctx = renderer.ctx;
const viewport = new MapViewport(canvas);
const geometry = new MapGeometry();
const client = new PreviewClient(base);
const roomsNode = document.querySelector('#rooms');
const filter = document.querySelector('#filter');
const objectInfo = new ObjectInfoPanel(document.querySelector('#object-info'));
let state;
let activeRoom;

function fit() {
  if (!state) return;
  viewport.fit(renderer.bounds(state.rooms, 100));
  draw();
}

function drawEntity(entity, room) {
  const x = room.x + entity.x;
  const y = room.y + entity.y;
  if (!renderer.drawSprite(entity.sprite, x, y)) {
    ctx.strokeStyle = '#f25b9a';
    ctx.lineWidth = 2 / viewport.scale;
    ctx.strokeRect(x - 4, y - 4, 8, 8);
  }
}

function draw() {
  if (!state) return;
  viewport.beginFrame(ctx);
  const box = renderer.bounds(state.rooms, 100);
  ctx.fillStyle = '#101010';
  ctx.fillRect(box.minX, box.minY, box.maxX - box.minX, box.maxY - box.minY);
  box.rooms.forEach(room => {
    ctx.fillStyle = room.name === activeRoom ? '#172b35' : '#080808';
    ctx.fillRect(room.x, room.y, room.width, room.height);
    renderer.tileRows(room, room.background, '#303941');
    renderer.tileRows(room, room.solids, '#bdc6cd');
    room.respawns.forEach(item => renderer.respawn(item, room));
    room.entities.forEach(entity => drawEntity(entity, room));
    ctx.strokeStyle = room.name === activeRoom ? '#54c6ff' : '#56616a';
    ctx.lineWidth = (room.name === activeRoom ? 2 : 1) / viewport.scale;
    ctx.strokeRect(room.x, room.y, room.width, room.height);
  });
  state.entrances.forEach(entrance => {
    const box = geometry.entranceBox(entrance);
    if (!box) return;
    ctx.strokeStyle = entrance.available ? '#ffad42' : '#775b37';
    ctx.lineWidth = 2 / viewport.scale;
    ctx.strokeRect(box.x, box.y, box.width || 4 / viewport.scale, box.height || 4 / viewport.scale);
  });
}

function resize() {
  renderer.resize();
  draw();
}

function renderList() {
  const query = filter.value.toLocaleLowerCase();
  roomsNode.replaceChildren();
  state.rooms.filter(room => room.name.toLocaleLowerCase().includes(query)).forEach(room => {
    const button = document.createElement('button');
    button.className = `room${room.name === activeRoom ? ' active' : ''}`;
    button.textContent = room.name;
    button.addEventListener('dblclick', () => reveal(room));
    roomsNode.append(button);
  });
}

function reveal(room) {
  activeRoom = room.name;
  viewport.center(room, true);
  renderList();
  draw();
}

function showObject(item, event) {
  objectInfo.show({ id: item.entityId ?? item.targetSid ?? item.kind ?? '',
    source: item.source ?? '', attrs: item.attrs }, event);
  if (item.targetSid && item.available) {
    objectInfo.addAction('打开目标地图', () => openMap(item.targetSid));
  }
}

async function openMap(targetSid) {
  try { accept(await client.request('open', { targetSid })); }
  catch (error) { status(error.message); }
}

async function navigate(action) {
  try { accept(await client.request(action)); }
  catch (error) { status(error.message); }
}

function accept(data) {
  state = data;
  geometry.load(state.rooms, state.entrances);
  interaction.cancel();
  document.querySelector('#title').textContent = state.title;
  document.querySelector('#back').disabled = !state.canBack;
  document.querySelector('#forward').disabled = !state.canForward;
  document.querySelector('#home').disabled = !state.canHome;
  activeRoom = undefined;
  objectInfo.hide();
  renderList();
  fit();
}

function status(message) {
  const node = document.querySelector('#status');
  node.textContent = message;
  node.hidden = false;
  setTimeout(() => { node.hidden = true; }, 2500);
}

const interaction = new CanvasInteraction(viewport, {
  redraw: draw,
  click: (point, event) => {
    if (!state || event.button !== 0) return;
    const entrance = geometry.entranceAt(point, { minimumSize: 4 / viewport.scale });
    if (entrance) return showObject(entrance, event);
    const item = geometry.entityAt(point, { radius: 8 / viewport.scale });
    if (item) showObject(item.entity, event);
  },
});

filter.addEventListener('input', renderList);
document.querySelector('#fit').addEventListener('click', fit);
document.querySelector('#back').addEventListener('click', () => navigate('back'));
document.querySelector('#forward').addEventListener('click', () => navigate('forward'));
document.querySelector('#home').addEventListener('click', () => navigate('home'));
addEventListener('resize', () => { resize(); objectInfo.constrain(); });
addEventListener('pagehide', () => { interaction.dispose(); client.leave('close'); });

resize();
client.request('state').then(accept).catch(error => status(error.message));
