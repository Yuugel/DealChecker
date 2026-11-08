(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const byId = (id) => document.getElementById(id);
  const API = '/api';
  const state = { data: null, watchById: new Map(), loading: false, formBusy: false, settingsBusy: false, search: '', expandedWatches: new Set() };
  const dialog = byId('watch-dialog');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }
  function fmtMoney(cents, currency = 'EUR') {
    if (!Number.isInteger(cents) || cents < 0) return 'Unbekannt';
    try { return new Intl.NumberFormat('de-DE', { style: 'currency', currency }).format(cents / 100); }
    catch { return `${(cents / 100).toFixed(2).replace('.', ',')} €`; }
  }
  function fmtTime(epoch, options = {}) {
    if (typeof epoch !== 'number' || !Number.isFinite(epoch) || epoch <= 0) return 'Noch keine Prüfung';
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short', ...options }).format(new Date(epoch * 1000));
  }
  function ageText(epoch) {
    if (!Number.isFinite(epoch) || epoch <= 0) return 'Noch nie geprüft';
    const mins = Math.max(0, Math.floor((Date.now() / 1000 - epoch) / 60));
    if (mins < 1) return 'Gerade eben geprüft';
    if (mins < 60) return `Vor ${mins} Min. geprüft`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `Vor ${hours} Std. geprüft`;
    return `Vor ${Math.floor(hours / 24)} Tagen geprüft`;
  }
  function splitList(text, maximum = 20) {
    return text.split(/[\n,;]/).map((part) => part.trim()).filter(Boolean).slice(0, maximum);
  }
  function moneyInputToCents(value, label) {
    const raw = value.trim().replace(/\s|€/g, '');
    if (!raw) return null;
    if (!/^\d{1,9}(?:[,.]\d{1,2})?$/.test(raw)) throw new Error(`${label}: Bitte einen positiven EUR-Betrag mit höchstens zwei Nachkommastellen eingeben.`);
    const normalized = raw.replace(',', '.');
    const cents = Math.round(Number(normalized) * 100);
    if (!Number.isSafeInteger(cents)) throw new Error(`${label}: Betrag ist zu groß.`);
    return cents;
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    const response = await fetch(`${API}${path}`, { ...options, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), cache: 'no-store' });
    const text = await response.text();
    let payload = null;
    if (text) {
      try { payload = JSON.parse(text); }
      catch { throw new Error(`Antwort des lokalen Dienstes ist ungültig (HTTP ${response.status}).`); }
    }
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      if (typeof payload?.detail === 'string') detail = payload.detail;
      else if (Array.isArray(payload?.detail)) {
        const messages = payload.detail.map(item => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean);
        if (messages.length) detail = messages.join(' · ');
      }
      throw new Error(detail);
    }
    return payload;
  }
  function toast(message, type = '') {
    const item = el('div', `toast ${type}`, message);
    item.setAttribute('role', 'status');
    byId('toast-region').append(item);
    window.setTimeout(() => item.remove(), 4200);
  }
  function setHealth(ok, label = '') {
    const wrap = byId('side-health');
    const dot = $('.health-dot', wrap);
    dot.classList.toggle('ok', ok);
    dot.classList.toggle('bad', !ok);
    $('strong', wrap).textContent = label || (ok ? 'Dienst verbunden' : 'Dienst nicht erreichbar');
    $('small', wrap).textContent = 'Lokaler Dienst';
  }
  function safeHttpUrl(raw) {
    if (typeof raw !== 'string' || !raw.trim()) return null;
    try {
      const parsed = new URL(raw);
      if ((parsed.protocol === 'https:' || parsed.protocol === 'http:') && parsed.hostname && !parsed.username && !parsed.password) return parsed.href;
    } catch { /* invalid product URL: render as unavailable */ }
    return null;
  }
  function offerState(offer) {
    const evaluation = String(offer?.evaluation || 'Unzureichende Daten');
    if (/Wunschpreis|Bestpreis|Sehr gutes|Preisrückgang/i.test(evaluation)) return ['good', evaluation];
    if (/Unzureichend|Kein relevantes/i.test(evaluation)) return ['warn', evaluation];
    return ['', evaluation];
  }
  function renderStats(data) {
    const watches = Array.isArray(data.watches) ? data.watches : [];
    const offers = Array.isArray(data.offers) ? data.offers : [];
    const events = Array.isArray(data.events) ? data.events : [];
    const sources = Array.isArray(data.sources) ? data.sources : [];
    byId('stat-watches').textContent = watches.filter(w => !w.paused).length;
    byId('stat-watches-foot').textContent = `${watches.filter(w => w.paused).length} pausiert · ${watches.length} insgesamt`;
    byId('stat-offers').textContent = offers.length;
    byId('stat-events').textContent = events.length;
    byId('stat-sources').textContent = sources.filter(s => s.configured !== false).length;
    byId('nav-watch-count').textContent = watches.length;
  }
  function renderWatchList(data) {
    const list = byId('watch-list');
    list.replaceChildren();
    const watches = Array.isArray(data.watches) ? data.watches : [];
    const offersByWatch = new Map();
    for (const offer of (Array.isArray(data.offers) ? data.offers : [])) {
      if (!offersByWatch.has(String(offer.watch_id))) offersByWatch.set(String(offer.watch_id), []);
      offersByWatch.get(String(offer.watch_id)).push(offer);
    }
    const query = state.search.toLocaleLowerCase('de-DE');
    const visible = watches.filter(w => !query || `${w.name || ''} ${w.query || ''} ${w.manufacturer || ''}`.toLocaleLowerCase('de-DE').includes(query));
    byId('watch-list-count').textContent = `${visible.length}`;
    if (!watches.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('div', 'empty-icon', '◎'), el('strong', '', 'Noch keine Beobachtungen'), el('p', '', 'Lege ein Produkt an, um echte Quellpreise und deren zeitlichen Verlauf lokal zu verfolgen.'));
      const button = el('button', 'button button-primary', '＋ Beobachtung anlegen'); button.type = 'button'; button.addEventListener('click', () => openWatchEditor()); empty.append(button); list.append(empty); return;
    }
    if (!visible.length) { list.append(el('div', 'empty-state', 'Keine Beobachtung passt zu deiner Suche.')); return; }
    visible.forEach((watch, index) => {
      const card = el('article', 'watch-card');
      card.dataset.watchId = String(watch.id);
      const main = el('div', 'watch-main');
      const product = el('div', 'product-title');
      const monogram = el('div', `product-monogram alt-${index % 4}`, String(watch.name || '?').trim().charAt(0).toLocaleUpperCase('de-DE') || '?'); monogram.setAttribute('aria-hidden', 'true');
      const copy = el('div', 'product-copy');
      copy.append(el('strong', '', watch.name || 'Ohne Namen'));
      const sourceNames = (watch.source_ids || []).map(id => (data.sources || []).find(s => s.id === id)?.name || id).join(', ');
      const watchOffers = (offersByWatch.get(String(watch.id)) || []).slice().sort((a, b) => {
        const staleOrder = Number(!!a.stale) - Number(!!b.stale);
        if (staleOrder) return staleOrder;
        const availabilityOrder = Number(a.availability !== 'in_stock') - Number(b.availability !== 'in_stock');
        if (availabilityOrder) return availabilityOrder;
        const aTotal = Number.isInteger(a.total_cents) ? a.total_cents : Number.POSITIVE_INFINITY;
        const bTotal = Number.isInteger(b.total_cents) ? b.total_cents : Number.POSITIVE_INFINITY;
        return aTotal - bTotal || (Number(b.checked_at) || 0) - (Number(a.checked_at) || 0);
      });
      const offer = watchOffers[0] || null;
      let productSubline = `${watch.kind === 'discovery' ? 'Entdecken' : 'Exakt'}${watch.query ? ` · ${watch.query}` : ''}${sourceNames ? ` · ${sourceNames}` : ''}`;
      if (offer) {
        const headline = offer.stale ? 'Letzter bekannter Treffer: ' : watchOffers.length > 1 && offer.total_cents != null ? 'Günstigster Treffer: ' : watchOffers.length > 1 ? 'Treffer: ' : '';
        productSubline += ` · ${headline}${offer.name || watch.name}`;
      }
      copy.append(el('small', '', productSubline));
      product.append(monogram, copy);

      const priceBox = el('div');
      if (offer) {
        const totalLabel = offer.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(offer.total_cents, offer.currency);
        const headlinePrice = offer.stale ? `Veraltet · ${totalLabel}` : watchOffers.length > 1 && offer.total_cents != null ? `Ab ${totalLabel}` : totalLabel;
        priceBox.append(el('span', 'current-price', headlinePrice));
        let detail = `${fmtMoney(offer.price_cents, offer.currency)} Artikel`;
        detail += offer.shipping_cents == null ? ' · Versand unbekannt' : offer.shipping_cents === 0 ? ' · Versand inklusive' : ` · + ${fmtMoney(offer.shipping_cents, offer.currency)} Versand`;
        if (Number.isInteger(offer.comparison_cents) && offer.comparison_cents >= 0) {
          detail += ` · beobachteter Vergleich ${fmtMoney(offer.comparison_cents, offer.currency)}`;
          if (Number.isInteger(offer.savings_cents) && offer.savings_cents > 0) detail += ` · ${fmtMoney(offer.savings_cents, offer.currency)} darunter`;
        }
        const details = el('small', 'price-detail', detail);
        priceBox.append(details);
      } else {
        priceBox.append(el('span', 'current-price', 'Noch kein Angebot'), el('small', 'price-detail', watch.last_error ? 'Letzte Prüfung mit Fehler' : 'Wird nach erster Prüfung angezeigt'));
      }
      const status = el('span', `offer-state ${offer ? offerState(offer)[0] : 'warn'}`, offer ? offerState(offer)[1] : (watch.paused ? 'Pausiert' : 'Noch nicht geprüft'));
      const actions = el('div', 'watch-actions');
      const check = el('button', 'small-action', 'Prüfen'); check.type = 'button'; check.setAttribute('aria-label', `${watch.name}: Jetzt prüfen`); check.disabled = !!watch.running || !!watch.paused || !!data.settings?.paused; check.title = check.disabled ? (watch.paused || data.settings?.paused ? 'Prüfungen sind pausiert' : 'Prüfung läuft bereits') : 'Jetzt manuell prüfen'; check.addEventListener('click', () => manualCheck(watch, check));
      const historyButton = el('button', 'small-action', 'Verlauf'); historyButton.type = 'button'; historyButton.setAttribute('aria-label', `${watch.name}: Preisverlauf anzeigen`); historyButton.disabled = !offer; historyButton.addEventListener('click', () => showHistory(offer, watch));
      const edit = el('button', 'small-action', 'Bearbeiten'); edit.type = 'button'; edit.addEventListener('click', () => openWatchEditor(watch));
      const remove = el('button', 'small-action', 'Entfernen'); remove.type = 'button'; remove.addEventListener('click', () => removeWatch(watch));
      actions.append(check, historyButton, edit, remove);
      main.append(product, priceBox, status, actions);

      const meta = el('div', 'watch-meta');
      const left = el('div', 'meta-left');
      const lastChecked = Number.isFinite(watch.last_checked) ? watch.last_checked : (offer?.checked_at ?? null);
      const age = el('span', `meta-item${offer?.stale ? ' stale' : ''}`);
      age.append(el('span', '', offer?.stale ? '⚠' : '◷'), el('strong', '', offer?.stale ? `Veraltet · ${ageText(lastChecked)}` : ageText(lastChecked)));
      left.append(age);
      if (watch.last_error) left.append(el('span', 'meta-item stale', `Letzter Fehler: ${watch.last_error}`));
      if (watch.target_cents != null) left.append(el('span', 'meta-item', `Wunschpreis ${fmtMoney(watch.target_cents)}`));
      if (watch.max_price_cents != null) left.append(el('span', 'meta-item', `Limit ${fmtMoney(watch.max_price_cents)}`));
      const right = el('div', 'meta-right');
      if (offer) {
        right.append(el('span', 'meta-item', offer.merchant ? `Quelle: ${offer.merchant}` : `Quelle: ${offer.source || 'Unbekannt'}`));
        if (offer.availability) right.append(el('span', 'meta-item', availabilityLabel(offer.availability, offer.provenance)));
      }
      if (watch.paused) right.append(el('span', 'meta-item', 'Pausiert'));
      meta.append(left, right);
      card.append(main, meta);
      if (watchOffers.length) {
        const disclosure = document.createElement('details');
        disclosure.className = 'watch-offers';
        disclosure.open = state.expandedWatches.has(String(watch.id));
        disclosure.addEventListener('toggle', () => {
          if (disclosure.open) state.expandedWatches.add(String(watch.id));
          else state.expandedWatches.delete(String(watch.id));
        });
        const summary = el('summary', 'offers-summary', `${watchOffers.length} Angebot${watchOffers.length === 1 ? '' : 'e'} anzeigen`);
        summary.setAttribute('aria-label', `${watchOffers.length} einzelne Angebote für ${watch.name} anzeigen oder ausblenden`);
        const offerList = el('div', 'individual-offers');
        watchOffers.forEach(item => offerList.append(renderOffer(item, watch)));
        disclosure.append(summary, offerList);
        card.append(disclosure);
      }
      list.append(card);
    });
  }
  function renderOffer(offer, watch) {
    const row = el('article', 'individual-offer');
    const heading = el('div', 'individual-offer-heading');
    const title = el('div', 'individual-offer-title');
    title.append(el('strong', '', offer.name || 'Unbenanntes Produkt'));
    title.append(el('small', '', offer.merchant || 'Händler unbekannt'));
    heading.append(title);
    const actions = el('div', 'individual-offer-actions');
    const history = el('button', 'small-action', 'Verlauf');
    history.type = 'button';
    history.setAttribute('aria-label', `Preisverlauf für ${offer.name || 'dieses Angebot'} anzeigen`);
    history.addEventListener('click', () => showHistory(offer, watch));
    actions.append(history);
    const href = safeHttpUrl(offer.url);
    if (href) {
      const link = el('a', 'small-action offer-link', 'Angebot ↗');
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      actions.append(link);
    }
    heading.append(actions);
    const price = el('div', 'individual-offer-price');
    price.append(el('strong', 'offer-total', offer.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(offer.total_cents, offer.currency)));
    price.append(el('span', '', `${fmtMoney(offer.price_cents, offer.currency)} Artikel`));
    price.append(el('span', '', offer.shipping_cents == null ? 'Versand unbekannt' : `Versand ${fmtMoney(offer.shipping_cents, offer.currency)}`));
    const chips = el('div', 'individual-offer-meta');
    chips.append(el('span', 'meta-chip', availabilityLabel(offer.availability, offer.provenance)));
    chips.append(el('span', 'meta-chip', `Quelle: ${offer.source || 'unbekannt'}`));
    const fromFeed = offer.provenance === 'feed' || offer.quality === 'feed_reported';
    chips.append(el('span', `meta-chip${offer.stale ? ' stale' : ''}`, `${offer.stale ? 'Veraltet · ' : ''}${fromFeed ? 'Katalogdaten · ' : ''}${ageText(offer.checked_at)}`));
    chips.append(el('span', 'meta-chip', `${fromFeed ? 'Feed-Zeitpunkt' : 'Geprüft'}: ${fmtTime(offer.checked_at)}`));
    if (Number.isInteger(offer.comparison_cents)) chips.append(el('span', 'meta-chip', `Beobachteter Vergleich ${fmtMoney(offer.comparison_cents, offer.currency)}`));
    if (Number.isInteger(offer.savings_cents)) chips.append(el('span', 'meta-chip', `Ersparnis ${fmtMoney(offer.savings_cents, offer.currency)}`));
    if (offer.coupon) chips.append(el('span', 'meta-chip offer-extra', `Gutschein: ${offer.coupon}`));
    if (offer.unit_price) chips.append(el('span', 'meta-chip offer-extra', `Grundpreis: ${offer.unit_price}`));
    row.append(heading, price, chips);
    return row;
  }
  function renderActivity(data) {
    const eventsNode = byId('events-list'); eventsNode.replaceChildren();
    const events = Array.isArray(data.events) ? data.events.slice(0, 8) : [];
    if (!events.length) eventsNode.append(el('div', 'list-empty', 'Noch keine relevanten Ereignisse beobachtet.'));
    for (const event of events) {
      const row = el('div', 'activity-row');
      const name = event.name || event.product || 'Preisereignis';
      row.append(el('strong', '', name));
      row.append(el('span', 'activity-value', event.total_cents == null ? 'Gesamt unbekannt' : fmtMoney(event.total_cents)));
      let eventDetail = `${event.reason || 'Beobachtet'} · ${fmtTime(event.checked_at || event.created_at)}`;
      if (event.notification_error) eventDetail += ` · ${event.notification_status === 'sent' ? 'Letzter Benachrichtigungsfehler' : 'Benachrichtigung: erneuter Versuch ausstehend'}${event.notification_attempts ? ` nach ${event.notification_attempts} Versuchen` : ''}: ${event.notification_error}`;
      else if (event.notification_status === 'pending') eventDetail += ' · Benachrichtigung ausstehend';
      else if (event.notification_status === 'sent') eventDetail += ' · Benachrichtigung gesendet';
      else if (event.notification_status === 'disabled') eventDetail += ' · Benachrichtigungen deaktiviert';
      row.append(el('small', '', eventDetail));
      const link = safeHttpUrl(event.url);
      if (link) { const anchor = el('a', 'activity-status', 'Angebot öffnen ↗'); anchor.href = link; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; row.append(anchor); }
      else row.append(el('span', 'activity-status', event.merchant || 'Lokal gespeichert'));
      eventsNode.append(row);
    }
    const checksNode = byId('checks-list'); checksNode.replaceChildren();
    const checks = Array.isArray(data.checks) ? data.checks.slice(0, 8) : [];
    if (!checks.length) checksNode.append(el('div', 'list-empty', 'Noch keine Quellenabfragen protokolliert.'));
    for (const check of checks) {
      const row = el('div', 'activity-row');
      row.append(el('strong', '', check.watch_name || check.name || `Beobachtung #${check.watch_id ?? '–'}`));
      const ok = check.status === 'ok';
      row.append(el('span', `activity-value${ok ? '' : ' bad-text'}`, ok ? `${Number(check.offers || 0)} Treffer` : (check.status || 'Fehler')));
      row.append(el('small', '', `${check.error || check.source_error || 'Quellenabfrage'} · ${fmtTime(check.checked_at || check.created_at || check.started_at)}`));
      row.append(el('span', 'activity-status', check.source || 'Lokaler Dienst'));
      checksNode.append(row);
    }
  }
  function renderSources(data) {
    const root = byId('sources-list'); root.replaceChildren();
    const sources = Array.isArray(data.sources) ? data.sources : [];
    if (!sources.length) { root.append(el('div', 'empty-state', 'Der Dienst meldet derzeit keine konfigurierten Quellen.')); return; }
    for (const source of sources) {
      const card = el('article', 'source-card'); const top = el('div', 'source-card-top');
      top.append(el('span', 'source-badge', String(source.name || source.id || '?').charAt(0).toLocaleUpperCase('de-DE')));
      const title = el('strong', '', source.name || source.id || 'Quelle'); top.append(title); card.append(top);
      card.append(el('p', '', source.description || 'Keine nähere Quellenbeschreibung verfügbar.'));
      const hasSuccess = Number.isFinite(source.last_success) && source.last_success > 0;
      const status = el('div', `source-status${source.last_error ? ' error' : ''}`);
      const dot = el('span', `health-dot${source.last_error ? ' bad' : hasSuccess ? ' ok' : ''}`); dot.setAttribute('aria-hidden', 'true'); status.append(dot);
      const statusText = source.configured === false ? 'Nicht konfiguriert' : source.last_error ? `Letzter Fehler: ${source.last_error}` : hasSuccess ? 'Quelle konfiguriert' : 'Quelle konfiguriert · noch nicht geprüft';
      status.append(document.createTextNode(statusText));
      const times = el('div', 'source-times');
      if (source.last_checked) times.append(el('span', '', `Letzte Prüfung: ${fmtTime(source.last_checked)}`));
      if (hasSuccess) times.append(el('span', '', `Letzte erfolgreiche Prüfung: ${fmtTime(source.last_success)}`));
      card.append(status, times);
      root.append(card);
    }
  }
  function renderSettings(data) {
    const settings = data.settings || {};
    byId('setting-paused').checked = !!settings.paused;
    const until = Number(settings.black_friday_until || 0);
    byId('setting-bf').checked = until > Date.now() / 1000;
    byId('bf-status').textContent = until > Date.now() / 1000 ? `Aktiv bis ${fmtTime(until)}.` : 'Der Modus ist nicht aktiv.';
    const configured = !!data.notification_configured;
    byId('setting-notifications').checked = !!settings.notification_enabled;
    byId('setting-notifications').disabled = !configured;
    byId('notification-status').textContent = configured ? 'Kanal ist konfiguriert; lokale Ereignisse werden unabhängig davon gespeichert.' : 'Kein Benachrichtigungskanal konfiguriert. Der Schalter ist daher nicht verfügbar.';
    byId('settings-feedback').textContent = '';
  }
  function render(data) {
    state.data = data;
    state.watchById = new Map((data.watches || []).map(w => [String(w.id), w]));
    setHealth(true, 'Lokaler Dienst bereit');
    const apple = (data.sources || []).find(source => source.id === 'apple');
    const coverage = byId('coverage-status');
    const coverageDot = $('.health-dot', coverage);
    const coverageText = apple?.last_error ? 'Letzter Quellenfehler' : Number.isFinite(apple?.last_success) && apple.last_success > 0 ? `Zuletzt erfolgreich ${fmtTime(apple.last_success)}` : apple?.configured === false ? 'Nicht konfiguriert' : 'Quelle konfiguriert · noch nicht geprüft';
    coverageDot.className = `health-dot${apple?.last_error ? ' bad' : Number.isFinite(apple?.last_success) && apple.last_success > 0 ? ' ok' : ''}`;
    coverage.lastChild.textContent = ` ${coverageText}`;
    renderStats(data); renderWatchList(data); renderActivity(data); renderSources(data); renderSettings(data);
    byId('updated-at').textContent = new Intl.DateTimeFormat('de-DE', { timeStyle: 'short' }).format(new Date());
  }
  async function refresh(showError = false) {
    if (state.loading) return;
    state.loading = true;
    try { render(await api('/state')); }
    catch (error) { setHealth(false); if (showError) toast(`Verbindung zum lokalen Dienst fehlgeschlagen: ${error.message}`, 'error'); }
    finally { state.loading = false; }
  }
  function clearForm() {
    byId('watch-form').reset();
    byId('watch-id').value = '';
    byId('watch-gtin').value = ''; byId('watch-model').value = ''; byId('watch-variant').value = '';
    byId('watch-category').value = ''; byId('watch-manufacturer').value = '';
    byId('watch-synonyms').value = ''; byId('watch-exclusions').value = '';
    byId('watch-merchants-include').value = ''; byId('watch-merchants-exclude').value = '';
    byId('watch-form-error').textContent = '';
    buildSourceOptions(state.data?.sources || []);
  }
  function buildSourceOptions(sources, selected = ['apple']) {
    const root = byId('source-options'); root.replaceChildren();
    const available = [...sources];
    for (const id of selected) {
      if (!available.some(source => source.id === id)) available.push({ id, name: `${id} · nicht im Quellenkatalog`, configured: false, description: '' });
    }
    for (const source of available) {
      const label = el('label', 'source-check');
      const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'watch-source'; input.value = source.id; input.checked = selected.includes(source.id); input.disabled = source.configured === false && !selected.includes(source.id);
      label.append(input, document.createTextNode(source.name || source.id)); root.append(label);
    }
    if (!sources.length) root.append(el('span', 'field-help', 'Der Dienst meldet noch keine verfügbaren Quellen.'));
  }
  function openWatchEditor(watch = null) {
    clearForm();
    byId('watch-dialog-title').textContent = watch ? 'Beobachtung bearbeiten' : 'Neue Beobachtung';
    byId('watch-save').textContent = watch ? 'Änderungen speichern' : 'Beobachtung speichern';
    if (watch) {
      byId('watch-id').value = watch.id;
      byId('watch-name').value = watch.name || '';
      const radio = $(`input[name="watch-kind"][value="${watch.kind === 'discovery' ? 'discovery' : 'exact'}"]`); if (radio) radio.checked = true;
      byId('watch-query').value = watch.query || '';
      buildSourceOptions(state.data?.sources || [], watch.source_ids || []);
      byId('watch-product-id').value = watch.product_id || '';
      byId('watch-url').value = watch.url || '';
      byId('watch-gtin').value = watch.gtin || '';
      byId('watch-model').value = watch.model || '';
      byId('watch-variant').value = watch.variant || '';
      byId('watch-category').value = watch.category || '';
      byId('watch-manufacturer').value = watch.manufacturer || '';
      byId('watch-synonyms').value = (watch.synonyms || []).join(', ');
      byId('watch-exclusions').value = (watch.exclusions || []).join(', ');
      byId('watch-merchants-include').value = (watch.merchants_include || []).join(', ');
      byId('watch-merchants-exclude').value = (watch.merchants_exclude || []).join(', ');
      byId('watch-target').value = watch.target_cents == null ? '' : (watch.target_cents / 100).toFixed(2).replace('.', ',');
      byId('watch-max').value = watch.max_price_cents == null ? '' : (watch.max_price_cents / 100).toFixed(2).replace('.', ',');
      byId('watch-priority').value = watch.priority || 'normal';
      byId('watch-interval').value = watch.interval_seconds == null ? '' : watch.interval_seconds;
      byId('watch-paused').checked = !!watch.paused;
    } else {
      buildSourceOptions(state.data?.sources || [], ['apple']);
    }
    dialog.showModal();
    byId('watch-name').focus();
  }
  function getWatchPayload() {
    const target = moneyInputToCents(byId('watch-target').value, 'Wunschpreis');
    const max = moneyInputToCents(byId('watch-max').value, 'Preislimit');
    const rawInterval = byId('watch-interval').value.trim();
    const interval = rawInterval ? Number(rawInterval) : null;
    if (interval !== null && (!Number.isSafeInteger(interval) || interval < 300 || interval > 86400)) throw new Error('Prüfintervall muss zwischen 300 und 86400 Sekunden liegen.');
    const sourceIds = $$('input[name="watch-source"]:checked').map(input => input.value);
    if (!sourceIds.length) throw new Error('Bitte mindestens eine konfigurierte Quelle auswählen.');
    if ((byId('watch-product-id').value.trim() || byId('watch-url').value.trim()) && sourceIds.length !== 1) throw new Error('Produkt-ID oder URL benötigt genau eine Quelle.');
    const url = byId('watch-url').value.trim();
    if (url && !safeHttpUrl(url)) throw new Error('Produkt-URL muss eine gültige HTTP- oder HTTPS-Adresse ohne Zugangsdaten sein.');
    return {
      name: byId('watch-name').value.trim(),
      kind: $('input[name="watch-kind"]:checked')?.value || 'exact',
      query: byId('watch-query').value.trim(), source_ids: sourceIds,
      product_id: byId('watch-product-id').value.trim(), url,
      gtin: byId('watch-gtin').value.trim(), model: byId('watch-model').value.trim(), variant: byId('watch-variant').value.trim(),
      synonyms: splitList(byId('watch-synonyms').value, 5), category: byId('watch-category').value.trim(), manufacturer: byId('watch-manufacturer').value.trim(),
      exclusions: splitList(byId('watch-exclusions').value), merchants_include: splitList(byId('watch-merchants-include').value), merchants_exclude: splitList(byId('watch-merchants-exclude').value),
      target_cents: target, max_price_cents: max,
      priority: byId('watch-priority').value, interval_seconds: interval, paused: byId('watch-paused').checked,
    };
  }
  async function saveWatch(event) {
    event.preventDefault();
    if (state.formBusy) return;
    const errorNode = byId('watch-form-error'); errorNode.textContent = '';
    let payload;
    try { payload = getWatchPayload(); }
    catch (error) { errorNode.textContent = error.message; return; }
    if (!payload.name) { errorNode.textContent = 'Bitte einen Anzeigenamen eingeben.'; byId('watch-name').focus(); return; }
    if (payload.kind === 'discovery' && !payload.query) { errorNode.textContent = 'Der Entdecken-Modus benötigt eine Suchanfrage.'; byId('watch-query').focus(); return; }
    if (payload.kind === 'exact' && !payload.query && !payload.product_id && !payload.url && !payload.gtin && !payload.model) {
      errorNode.textContent = 'Für eine exakte Beobachtung Suchanfrage, Produkt-ID, URL, GTIN oder Modell eintragen. Für Titelabgleich den Produktnamen auch unter Suchanfrage eingeben.';
      byId('watch-query').focus(); return;
    }
    if (payload.target_cents != null && payload.max_price_cents != null && payload.target_cents > payload.max_price_cents) { errorNode.textContent = 'Der Wunschpreis liegt über dem Preislimit.'; return; }
    state.formBusy = true; byId('watch-save').disabled = true;
    try {
      const id = byId('watch-id').value;
      await api(id ? `/watches/${encodeURIComponent(id)}` : '/watches', { method: id ? 'PUT' : 'POST', body: payload });
      dialog.close(); toast(id ? 'Beobachtung aktualisiert.' : 'Beobachtung angelegt.', 'success'); await refresh();
    } catch (error) { errorNode.textContent = error.message; }
    finally { state.formBusy = false; byId('watch-save').disabled = false; }
  }
  async function removeWatch(watch) {
    if (!window.confirm(`„${watch.name}“ und die zugehörige Beobachtung entfernen?`)) return;
    try { await api(`/watches/${encodeURIComponent(watch.id)}`, { method: 'DELETE' }); toast('Beobachtung entfernt.', 'success'); await refresh(); }
    catch (error) { toast(`Entfernen fehlgeschlagen: ${error.message}`, 'error'); }
  }
  async function manualCheck(watch, button) {
    button.disabled = true; const original = button.textContent; button.textContent = 'Läuft …';
    try {
      const result = await api(`/watches/${encodeURIComponent(watch.id)}/check`, { method: 'POST', body: {} });
      if (result.status === 'ok') toast(`Prüfung abgeschlossen · ${Number(result.offers || 0)} Treffer.`, 'success');
      else toast(`Prüfung ${result.status || 'beendet'}${result.error ? `: ${result.error}` : '.'}`, result.status === 'busy' ? '' : 'error');
      await refresh();
    } catch (error) { toast(`Prüfung fehlgeschlagen: ${error.message}`, 'error'); }
    finally { button.textContent = original; }
  }
  async function showHistory(offer, watch) {
    const historyDialog = byId('history-dialog');
    byId('history-title').textContent = `Preisverlauf · ${watch.name || offer.name || 'Produkt'}`;
    byId('history-subtitle').textContent = `${offer.merchant || offer.source || 'Quelle'} · Gesamtpreis nur bei bekanntem Versand`;
    const rows = byId('history-rows'); rows.replaceChildren();
    byId('history-chart').replaceChildren();
    try {
      const history = await api(`/offers/${encodeURIComponent(offer.id)}/history`);
      const observations = Array.isArray(history) ? history : [];
      for (const item of observations) {
        const row = document.createElement('tr');
        const fromFeed = item.provenance === 'feed';
        const values = [`${fromFeed ? 'Katalog-Beobachtung · ' : ''}${fmtTime(item.checked_at)}`, fmtMoney(item.price_cents), item.shipping_cents == null ? 'Unbekannt' : fmtMoney(item.shipping_cents), item.total_cents == null ? 'Unbekannt' : fmtMoney(item.total_cents), availabilityLabel(item.availability, item.provenance), item.provenance || 'Quellenbeobachtung'];
        values.forEach(value => row.append(el('td', '', value)));
        rows.append(row);
      }
      renderChart(observations);
      if (!observations.length) rows.append(emptyTableRow('Für dieses Angebot liegen noch keine gespeicherten Beobachtungen vor.'));
    } catch (error) { rows.append(emptyTableRow(`Verlauf konnte nicht geladen werden: ${error.message}`)); showChartNote('Verlauf konnte nicht geladen werden.'); }
    historyDialog.showModal();
  }
  function availabilityLabel(value, provenance = '') {
    const fromFeed = provenance === 'feed';
    if (value === 'in_stock') return fromFeed ? 'Feed meldet verfügbar' : 'Verfügbar';
    if (value === 'out_of_stock') return fromFeed ? 'Feed meldet nicht verfügbar' : 'Nicht verfügbar';
    return fromFeed ? 'Feed: Verfügbarkeit unbekannt' : 'Unbekannt';
  }
  function emptyTableRow(message) { const row = document.createElement('tr'); const cell = el('td', '', message); cell.colSpan = 6; row.append(cell); return row; }
  function showChartNote(message) {
    const root = byId('history-chart'); root.replaceChildren();
    const svg = svgEl('svg', { viewBox: '0 0 700 220', role: 'img', 'aria-label': message });
    const text = svgEl('text', { x: 350, y: 112, 'text-anchor': 'middle', class: 'chart-note' }); text.textContent = message; svg.append(text); root.append(svg);
  }
  function renderChart(observations) {
    const known = observations.filter(item => Number.isInteger(item.total_cents) && item.total_cents >= 0 && Number.isFinite(item.checked_at)).sort((a, b) => a.checked_at - b.checked_at);
    if (known.length < 2) { showChartNote(known.length === 1 ? 'Eine Gesamtpreisbeobachtung: Für einen Verlauf sind mindestens zwei nötig.' : 'Noch keine ausreichenden Daten mit bekanntem Gesamtpreis.'); return; }
    const width = 700, height = 220, pad = { left: 64, right: 20, top: 20, bottom: 37 };
    const minT = known[0].checked_at, maxT = known[known.length - 1].checked_at;
    let minP = Math.min(...known.map(item => item.total_cents)), maxP = Math.max(...known.map(item => item.total_cents));
    if (minP === maxP) { minP = Math.max(0, minP - 100); maxP += 100; }
    const x = (item) => pad.left + (maxT === minT ? .5 : (item.checked_at - minT) / (maxT - minT)) * (width - pad.left - pad.right);
    const y = (item) => pad.top + (1 - (item.total_cents - minP) / (maxP - minP)) * (height - pad.top - pad.bottom);
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `Beobachteter Gesamtpreis in ${known.length} Messungen; von ${fmtMoney(known[0].total_cents)} bis ${fmtMoney(known[known.length - 1].total_cents)}.` });
    for (let i = 0; i < 4; i++) {
      const ratio = i / 3, gy = pad.top + ratio * (height - pad.top - pad.bottom);
      svg.append(svgEl('line', { x1: pad.left, x2: width - pad.right, y1: gy, y2: gy, class: 'chart-grid' }));
      const value = Math.round(maxP - ratio * (maxP - minP));
      const label = svgEl('text', { x: pad.left - 8, y: gy + 4, 'text-anchor': 'end' }); label.textContent = fmtMoney(value); svg.append(label);
    }
    const points = known.map(item => `${x(item)},${y(item)}`).join(' ');
    const area = `${pad.left},${height - pad.bottom} ${points} ${width - pad.right},${height - pad.bottom}`;
    svg.append(svgEl('polygon', { points: area, class: 'chart-area' }));
    svg.append(svgEl('polyline', { points, class: 'chart-line' }));
    known.forEach((item) => {
      const circle = svgEl('circle', { cx: x(item), cy: y(item), r: 4.5, class: 'chart-point' }); circle.setAttribute('aria-label', `${fmtTime(item.checked_at)}: ${fmtMoney(item.total_cents)}`); svg.append(circle);
    });
    const first = svgEl('text', { x: pad.left, y: height - 11 }); first.textContent = fmtTime(minT, { dateStyle: 'short', timeStyle: undefined }); svg.append(first);
    const last = svgEl('text', { x: width - pad.right, y: height - 11, 'text-anchor': 'end' }); last.textContent = fmtTime(maxT, { dateStyle: 'short', timeStyle: undefined }); svg.append(last);
    byId('history-chart').replaceChildren(svg);
  }
  async function updateSettings(key, value, feedback) {
    const status = byId('settings-feedback');
    try {
      const settings = { [key]: value };
      await api('/settings', { method: 'PUT', body: settings });
      status.textContent = feedback;
      await refresh();
    } catch (error) { status.textContent = `Einstellung konnte nicht gespeichert werden: ${error.message}`; toast(status.textContent, 'error'); await refresh(); }
  }
  function bind() {
    byId('watch-form').addEventListener('submit', saveWatch);
    byId('new-watch-button').addEventListener('click', () => openWatchEditor());
    byId('new-watch-button-secondary').addEventListener('click', () => openWatchEditor());
    $$('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog')?.close()));
    [dialog, byId('history-dialog')].forEach(modal => modal.addEventListener('click', event => { if (event.target === modal) modal.close(); }));
    byId('watch-search').addEventListener('input', event => { state.search = event.target.value; if (state.data) renderWatchList(state.data); });
    byId('refresh-button').addEventListener('click', () => refresh(true));
    byId('activity-refresh').addEventListener('click', () => refresh(true));
    byId('setting-paused').addEventListener('change', event => updateSettings('paused', event.target.checked, event.target.checked ? 'Automatische Prüfungen pausiert.' : 'Automatische Prüfungen fortgesetzt.'));
    byId('setting-bf').addEventListener('change', event => updateSettings('black_friday_until', event.target.checked ? Math.floor(Date.now() / 1000) + 86400 : 0, event.target.checked ? '24‑Stunden‑Modus für 24 Stunden aktiviert.' : '24‑Stunden‑Modus beendet.'));
    byId('setting-notifications').addEventListener('change', event => updateSettings('notification_enabled', event.target.checked, event.target.checked ? 'Benachrichtigungen aktiviert.' : 'Benachrichtigungen deaktiviert.'));
    $$('.nav-link').forEach(link => link.addEventListener('click', () => { $$('.nav-link').forEach(item => item.classList.toggle('active', item === link)); }));
  }
  bind();
  refresh();
  window.setInterval(() => refresh(), 10000);
})();
