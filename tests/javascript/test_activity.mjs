// Activity's filters, search, counts and daily chart run on the server now (tests/python/test_order_views.py); the
// browser builds the paged reads and sends the viewer's time zone.
import assert from 'node:assert/strict';
import {pageUrls, PAGE_LIMIT} from '../../static/js/remote.js';
import {viewerTimeZone, STATUS_FILTERS} from '../../static/js/activity.js';

const parse = url => { const [path, query] = url.split('?'); return [path, Object.fromEntries(new URLSearchParams(query))]; };

// One read for a first page; empty filters are left out; spaces and odd characters are encoded.
assert.deepEqual(pageUrls('/api/orders', {query: 'rally bitcoin', status: '', agent: 'a&b', category: undefined}, 50).map(parse),
  [['/api/orders', {query: 'rally bitcoin', agent: 'a&b', offset: '0', limit: '50'}]]);
assert.match(pageUrls('/api/orders', {status: 'awaiting approval'}, 20)[0], /status=awaiting\+approval/);
// More than the server's per-read limit splits into reads of at most PAGE_LIMIT, in order.
assert.equal(PAGE_LIMIT, 100);
assert.deepEqual(pageUrls('/api/events', {}, 250).map(url => parse(url)[1]),
  [{offset: '0', limit: '100'}, {offset: '100', limit: '100'}, {offset: '200', limit: '50'}]);
assert.deepEqual(pageUrls('/api/events', {}, 100).length, 1);
assert.deepEqual(pageUrls('/api/events', {}, 0), []);

// The daily chart groups by the viewer's calendar days, so the browser names its IANA zone.
assert.match(viewerTimeZone(), /^[A-Za-z_]+(\/[A-Za-z0-9_+-]+)*$/);
assert.ok(STATUS_FILTERS.includes('awaiting approval'));
console.log('Activity tests passed: paged reads, time zone');
