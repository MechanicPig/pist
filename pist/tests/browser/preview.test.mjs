import assert from 'node:assert/strict';
import test from 'node:test';
import { EditState } from '../../src/pist/map_preview/static/edit_state.js';
import { MapOverlays } from '../../src/pist/map_preview/static/overlays.js';
import { MapGeometry } from '../../../berries/src/berries/map_preview/static/geometry.js';

function state(readOnly = false) {
  return { readOnly, selected: ['start'], rooms: [
    { name: 'start', x: 0, y: 0, width: 100, height: 100, roomCount: 2,
      background: [], solids: [], respawns: [], firstClearTime: 1000, firstClearCumulativeTime: 1000,
      entities: [{ x: 10, y: 10, kind: 'strawberry', key: 'one', summary: { kind: 'berry', label: '草莓', stat: 'count' } }] },
    { name: 'end', x: 100, y: 0, width: 100, height: 100,
      background: [], solids: [], respawns: [], firstClearTime: 2000, firstClearCumulativeTime: 3000,
      entities: [{ x: 10, y: 10, kind: 'strawberry', key: 'two', excluded: true, summary: { kind: 'berry', label: '草莓', stat: 'count' } }] },
  ], firstClearRooms: ['start', 'end'], entrances: [] };
}

test('edits track selection, counts and exclusions, and load restores the saved snapshot', () => {
  const edit = new EditState();
  assert.equal(edit.dirty, false);
  edit.load(state());
  assert.equal(edit.dirty, false);
  assert.equal(edit.roomCount, 2);
  edit.toggleRoom('end');
  edit.setRoomCount('end', 3);
  edit.toggleEntity('two');
  assert.equal(edit.dirty, true);
  assert.deepEqual(edit.payload(), { rooms: ['start', 'end'], roomCounts: { start: 2, end: 3 }, excludedEntities: [] });
  edit.markSaved();
  assert.equal(edit.dirty, false);
  edit.toggleRoom('start');
  edit.load(state());
  assert.deepEqual(edit.payload(), { rooms: ['start'], roomCounts: { start: 2 }, excludedEntities: ['two'] });
  assert.equal(edit.dirty, false);
});

test('selection order is not an edit; changing an unselected count is not an edit until selected', () => {
  const edit = new EditState();
  const next = state();
  next.selected = ['start', 'end'];
  edit.load(next);
  edit.toggleRoom('start');
  edit.toggleRoom('start');
  assert.equal(edit.dirty, false);
  edit.load(state());
  edit.setRoomCount('end', 5);
  assert.equal(edit.dirty, false);
  edit.toggleRoom('end');
  assert.equal(edit.dirty, true);
});

test('box selection supports add, subtract and symmetric difference; counts stay positive integers', () => {
  const edit = new EditState();
  edit.load(state());
  edit.selectRooms(edit.rooms, {});
  assert.deepEqual([...edit.selected], ['start', 'end']);
  edit.selectRooms([edit.rooms[0]], { ctrlKey: true });
  assert.deepEqual([...edit.selected], ['end']);
  edit.selectRooms(edit.rooms, { ctrlKey: true, shiftKey: true });
  assert.deepEqual([...edit.selected], ['start']);
  for (const value of [0, -2, 1.5, NaN]) {
    edit.setRoomCount('start', value);
    assert.equal(edit.roomCount, 1);
  }
});

test('read-only state is never dirty and summary changes follow exclusions', () => {
  const edit = new EditState();
  edit.load(state(true));
  assert.equal(edit.collectibleSummaryText(), '草莓 1/2');
  edit.toggleEntity('two');
  assert.equal(edit.collectibleSummaryText(), '草莓 2/2');
  assert.equal(edit.dirty, false);
});

test('exist and select summaries preserve absence and conflicting values', () => {
  const edit = new EditState();
  const next = state();
  next.rooms[0].entities = [
    { key: 'cassette', summary: { kind: 'cassette', label: '磁带', stat: 'exist' } },
    { key: 'end-heart', summary: { kind: 'heart', label: '心', stat: 'select', value: 'end' } },
  ];
  next.rooms[1].entities = [
    { key: 'extra-heart', summary: { kind: 'heart', label: '心', stat: 'select', value: 'extra' } },
  ];
  edit.load(next);
  assert.equal(edit.collectibleSummaryText(), '磁带 有（1/1） · 心 冲突（end / extra）');
  edit.toggleEntity('cassette');
  edit.toggleEntity('extra-heart');
  assert.equal(edit.collectibleSummaryText(), '磁带 无（0/1） · 心 end（1/2）');
  edit.toggleEntity('end-heart');
  assert.equal(edit.collectibleSummaryText(), '磁带 无（0/1） · 心 无');
});

test('canvas labels switch between room and cumulative time without changing list data', () => {
  const texts = [];
  const boxes = [];
  const ctx = new Proxy({}, { get: (target, key) => target[key] ?? (() => {}), set: (target, key, value) => { target[key] = value; return true; } });
  ctx.fillText = text => texts.push(text);
  ctx.strokeRect = (...box) => boxes.push(box);
  ctx.measureText = () => ({ width: 20 });
  const renderer = { ctx, bounds: rooms => ({ rooms, minX: 0, minY: 0, maxX: 200, maxY: 100 }),
    tileRows() {}, respawn() {}, drawSprite: () => true };
  const viewport = { scale: 1, beginFrame() {} };
  const next = state();
  next.entrances = [{ room: 'end', x: 10, y: 20, width: 30, height: 40, targetTitle: 'target', available: true }];
  const geometry = new MapGeometry();
  geometry.load(next.rooms, next.entrances);
  const overlays = new MapOverlays(renderer, viewport, geometry);
  const edit = new EditState();
  edit.load(next);
  overlays.draw({ state: next, mode: 'preview', edit, showRoute: true, showTime: true, timeMode: 'room' });
  assert.ok(texts.includes('0:00:02'));
  assert.ok(texts.includes('target'));
  assert.ok(boxes.some(box => box.join(',') === '110,20,30,40'));
  texts.length = 0;
  overlays.draw({ state: next, mode: 'preview', edit, showRoute: true, showTime: true, timeMode: 'cumulative' });
  assert.ok(texts.includes('0:00:03'));
  assert.equal(next.rooms[1].firstClearTime, 2000);
});
